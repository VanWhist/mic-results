"""第5層: FIS 公式サイトの HTML 結果との外部照合（要ネットワーク）。

FIS のレース結果ページには選手ごとに「最終順位」と「最後に滑ったラウンドのラン得点」だけが載る
（fis_fetch.fetch_html_results の docstring 参照）。そこで各大会・性別について、PDF 側から同じ
2つを再構成して突き合わせる。あわせて、その大会の添付 PDF の URL を round.source.fis_url に入れる。

注意: FIS の PDF と HTML は同じ結果システムから生成されている可能性が高く、完全に独立した
第三者ソースではない（ChatGPT レビュー 2026-09-03）。「自分が正しく転記したか」の確認として使う。
"""
import collections
from decimal import Decimal
from . import fis_fetch
from .verify import Finding

ROUND_ORDER = ['Q', 'Q1', 'Q2', 'F1', 'F2', 'F3']


def season_code(season):
    return int(season.split('-')[0]) + 1


def overall_from_pdf(rounds_by_code):
    """ICR 3702: finals first, then earlier phases. Returns {fis_code: (rank, last_score)}."""
    order = [c for c in ('F3', 'F2', 'F1', 'Q2', 'Q', 'Q1') if c in rounds_by_code]
    out = {}
    placed = []
    for code in order:
        rnd, runs = rounds_by_code[code]
        by_ath = collections.OrderedDict()
        for r in runs:
            by_ath.setdefault(r['fis_code'], []).append(r)
        items = []
        for fis, blocks in by_ath.items():
            if fis in out:
                continue
            first = blocks[0]
            score = first['best_score'] if first.get('q_block') else first['run_score']
            if score is None:
                ok = [b['run_score'] for b in blocks if b['run_score'] is not None]
                score = max(ok) if ok else None
            items.append((first['rank'] if first['rank'] else 10 ** 6, fis, score))
        items.sort(key=lambda x: x[0])
        for rank_in_round, fis, score in items:
            out[fis] = {'round': code, 'round_rank': rank_in_round if rank_in_round < 10 ** 6 else None,
                        'score': score, 'overall': len(placed) + 1 if rank_in_round < 10 ** 6 else None}
            placed.append(fis)
    return out


def cross_check(rounds_ctx, log=print, known_gaps=None, stats=None):
    """stats (dict, optional) receives: groups_expected, groups_checked, and per-round status
    ('ok' | 'upstream_missing' | 'error') in stats['round_status']."""
    known_gaps = known_gaps or {}
    stats = stats if stats is not None else {}
    stats.setdefault('groups_expected', 0); stats.setdefault('groups_checked', 0); stats.setdefault('round_status', {})
    findings = []
    groups = collections.defaultdict(dict)
    for ctx in rounds_ctx:
        r = ctx['round']
        groups[(r['season'], r['series'], r['event_id'], r['gender'])][r['round']] = (r, ctx['runs'])
    cache = {}
    for (season, series, event_id, gender), rbc in sorted(groups.items()):
        key = (season_code(season), series)
        if key not in cache:
            try:
                cache[key] = fis_fetch.list_events(*key)
            except Exception as e:
                findings.append(Finding('warning', event_id, 'layer5', f"FIS 大会一覧を取得できない {key}: {e!r}"))
                cache[key] = []
        races = cache[key]
        stats['groups_expected'] += 1
        codices = {r['codex'] for r, _ in rbc.values()}
        my = [x for x in races if x['codex'] in codices and x['gender'] == gender]
        if not my:
            # not finding the race is an error, not a warning: a silent "0 compared" must never count as green
            findings.append(Finding('error', event_id, 'layer5', f"FIS サイトに codex {sorted(codices)} ({gender}) のレースが見つからない（照合できない）"))
            for r, _ in rbc.values():
                stats['round_status'][r['round_id']] = 'error'
            continue
        # attachments -> fis_url for each round
        for race in my:
            try:
                pdfs = fis_fetch.list_result_pdfs(race['race_url'], race['codex'])
            except Exception as e:
                findings.append(Finding('warning', event_id, 'layer5', f"添付一覧を取得できない codex {race['codex']}: {e!r}"))
                pdfs = []
            for p in pdfs:
                for code, (r, _) in rbc.items():
                    if r['codex'] == race['codex'] and p['round'] == code:
                        r['source']['fis_url'] = p['url']
        # HTML results of the main race (the one that is not a separate qualification codex)
        main = [x for x in my if x['race_kind'].lower() == 'moguls'] or my
        race = main[-1]
        try:
            html = fis_fetch.fetch_html_results(race['race_url'])
        except Exception as e:
            html = []
        if not html:
            findings.append(Finding('error', event_id, 'layer5', f"{gender} HTML 結果を取得できない（0 行）: 照合未実行"))
            for r, _ in rbc.values():
                stats['round_status'][r['round_id']] = 'error'
            continue
        pdf_side = overall_from_pdf(rbc)
        html_by = {h['fis_code']: h for h in html if h['fis_code']}
        gap = known_gaps.get(event_id, {}).get(gender)
        level = 'warning' if gap else 'error'
        if gap:
            findings.append(Finding('warning', event_id, 'layer5', f"{gender} 既知の欠落: {'; '.join(f'{k}: {v}' for k, v in gap.items())}"))
        n_cmp = 0
        for fis, h in html_by.items():
            p = pdf_side.get(fis)
            if p is None:
                findings.append(Finding('warning', event_id, 'layer5', f"{gender} {h['name']} が FIS HTML にあるが PDF 側に無い"))
                continue
            n_cmp += 1
            if h['run_score'] is not None and p['score'] is not None and abs(Decimal(str(h['run_score'])) - Decimal(str(p['score']))) > Decimal('0.005'):
                findings.append(Finding(level, event_id, 'layer5', f"{gender} {h['name']} 得点 HTML {h['run_score']} / PDF({p['round']}) {p['score']}"))
            if h['rank'] is not None and p['overall'] is not None and h['rank'] != p['overall'] and p['round'].startswith('F'):
                findings.append(Finding(level, event_id, 'layer5', f"{gender} {h['name']} 最終順位 HTML {h['rank']} / PDF 再構成 {p['overall']}"))
        for fis, p in pdf_side.items():
            if fis not in html_by:
                findings.append(Finding('warning', event_id, 'layer5', f"{gender} FIS {fis} が PDF にあるが HTML に無い"))
        log(f"  第5層 {event_id} {gender}: HTML {len(html_by)} 名 / 比較 {n_cmp} 名")
        if n_cmp == 0:
            findings.append(Finding('error', event_id, 'layer5', f"{gender} 比較できた選手が 0 名: 照合未実行"))
            status = 'error'
        else:
            stats['groups_checked'] += 1
            status = 'upstream_missing' if gap else 'ok'
        for r, _ in rbc.values():
            stats['round_status'][r['round_id']] = status
    return findings
