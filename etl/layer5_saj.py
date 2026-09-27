"""第5層（SAJ 系）: SAJ 競技データバンクの順位表 HTML との外部照合。

順位表ページ（registry の pdfs[].page_url、例 https://sajdb.shikuminet.jp/freestyle/2026/competition/0443/result）には
選手ごとに「最終順位」「SAJ 番号」「氏名」「最後に滑ったラウンドの得点」が載る。ブラウザで人の速度で開いて表を
JSON に写したもの（etl/layer5_cache/saj/<seasoncode>_<codex>.json、{'url','rows':[{'rank','bib','code','name','club','pref','score'}]}）
と、PDF 側から再構成した総合順位（決勝→準決勝→予選の順に並べる）を突き合わせる。

注意: PDF も順位表も同じ SAJ の結果システム由来で、独立した第三者ソースではない。「自分が正しく転記したか」の確認として使う。
"""
import collections, os, re, json, unicodedata
from decimal import Decimal
from . import config
from .verify import Finding, _num_eq

CACHE_DIR = os.path.join(config.HERE, 'layer5_cache', 'saj')
ROUND_ORDER_DESC = ['F3', 'F2', 'F1', 'Q2', 'Q', 'Q1']


def cache_key(page_url):
    """'https://sajdb.shikuminet.jp/freestyle/2026/competition/0443/result' -> '2026_0443'"""
    m = re.search(r'/freestyle/(\d{4})/competition/(\d+)/result', page_url or '')
    return f"{m.group(1)}_{m.group(2)}" if m else None


def load_cache(page_url):
    key = cache_key(page_url)
    if not key:
        return None
    p = os.path.join(CACHE_DIR, key + '.json')
    if not os.path.exists(p):
        return None
    with open(p, encoding='utf-8') as fh:
        return json.load(fh)


def overall_from_rounds(rounds_by_code):
    """{athlete_id: {'overall', 'round', 'round_rank', 'score', 'saj_no', 'name'}}。決勝から順に、未配置の選手を順位順に並べる。"""
    out = {}
    placed = 0
    order = [c for c in ROUND_ORDER_DESC if c in rounds_by_code]
    for code in order:
        rnd, runs = rounds_by_code[code]
        items = []
        for r in runs:
            if r['athlete_id'] in out or not r.get('counting', True):
                continue
            items.append((r['rank'] if r['rank'] else 10 ** 6, r))
        items.sort(key=lambda x: x[0])
        ranked_items = [(rk, r) for rk, r in items if rk < 10 ** 6]
        unranked = [r for rk, r in items if rk >= 10 ** 6]
        # ラウンド内の印字の順位の間隔をそのまま総合に持ち込む：同じ順位（決勝の DNF 2 人に「11」）は同順位、
        # 欠番（2020 FIS DM 女子は 25 位が無く 26 位）は欠番のまま。ブロックの先頭（未配置の最小順位）からの差で数える
        base = placed
        first = min((rk for rk, _ in ranked_items), default=1)
        for rank_in_round, r in ranked_items:
            placed += 1
            overall = base + 1 + (rank_in_round - first)
            out[r['athlete_id']] = {'overall': overall, 'round': code, 'round_rank': rank_in_round,
                                    'score': r['run_score'], 'saj_no': r.get('saj_no'), 'name': r['name'], 'status': r['status']}
        # 決勝で DNF/DNS になった選手は、SAJ の順位表では決勝ブロックの末尾に同順位で載る（予選落ちの選手より上）
        if unranked and code != order[-1]:
            shared = placed + 1
            for r in unranked:
                out[r['athlete_id']] = {'overall': shared, 'round': code, 'round_rank': None,
                                        'score': r['run_score'], 'saj_no': r.get('saj_no'), 'name': r['name'], 'status': r['status']}
            placed += len(unranked)
        else:
            for r in unranked:
                out[r['athlete_id']] = {'overall': None, 'round': code, 'round_rank': None,
                                        'score': r['run_score'], 'saj_no': r.get('saj_no'), 'name': r['name'], 'status': r['status']}
    return out


def _norm_no(s):
    s = re.sub(r'\D', '', str(s or ''))
    return s.lstrip('0') or None


def _norm_name(s):
    # 順位表は外国籍選手の名前を全角英字で載せることがある（'Ｂｒａｙｄｅｎ Ｋｕｒｏｄａ'）ので NFKC で揃える
    return ''.join(unicodedata.normalize('NFKC', s or '').split()).lower()


def _dec(s):
    """順位表の得点欄 → Decimal。空欄・'-'・'DNF' などは None"""
    try:
        return Decimal(str(s).strip())
    except Exception:  # noqa
        return None


def compare(event_id, gender, rounds_by_code, cache):
    """戻り値: (findings, status)。status は 'ok' | 'error' | 'upstream_missing'"""
    rows = cache.get('rows') or []
    if not rows:
        return [], 'upstream_missing'
    ours = overall_from_rounds(rounds_by_code)
    round_id = rounds_by_code[[c for c in ROUND_ORDER_DESC if c in rounds_by_code][0]][0]['round_id']
    if all(rnd.get('discipline') == 'DM' for rnd, _ in rounds_by_code.values()):
        return compare_dm(gender, round_id, ours, rows)
    f = []
    # FIS・アジアカップの一部は SAJ データバンクの PDF が決勝だけで、順位表には予選で終わった選手も載る。
    # 予選のラウンドが無いときは、決勝に居ない下位の選手が PDF 側に居ないのは当然なので警告にとどめる
    has_q = any(c in rounds_by_code for c in ('Q', 'Q1', 'Q2'))
    n_ranked = sum(1 for v in ours.values() if v['overall'] is not None)
    by_no = {_norm_no(v['saj_no']): k for k, v in ours.items() if v.get('saj_no')}
    by_name = {_norm_name(v['name']): k for k, v in ours.items()}
    matched = []
    used = set()
    for row in rows:
        aid = by_no.get(_norm_no(row.get('code'))) or by_name.get(_norm_name(row.get('name')))
        if aid is None and not _norm_no(row.get('code')) and _dec(row.get('score')) is not None:
            # SAJ 番号の無い外国籍選手は、順位表がカタカナ（'ノイズ キース'）、PDF がローマ字で名前が突き合わない。
            # 得点が同じで、まだ誰とも対応していない選手がちょうど 1 人なら同じ選手とみなす
            cands = [k for k, v in ours.items() if k not in used and v.get('score') is not None
                     and _num_eq(_dec(row.get('score')), v['score']) and not v.get('saj_no')]
            if len(cands) == 1:
                aid = cands[0]
                f.append(Finding('warning', round_id, 'layer5', f"{gender} 順位表の {row.get('name')} を得点 {row.get('score')} で PDF の {ours[aid]['name']} と対応づけた（名前の表記が違う）"))
        if aid is None:
            # 順位が空欄の行（棄権などで順位の付かない選手）は結果ではないので、PDF に居なくても警告にとどめる
            level = 'error' if str(row.get('rank') or '').strip() else 'warning'
            note = ''
            if level == 'error' and not has_q and str(row['rank']).strip().isdigit() and int(row['rank']) > n_ranked:
                level, note = 'warning', '（PDF は決勝だけで予選が無い）'
            f.append(Finding(level, round_id, 'layer5', f"{gender} 順位表の {row.get('rank') or '（順位なし）'}位 {row.get('name')}（{row.get('code')}）が PDF 側に居ない{note}"))
            continue
        used.add(aid)
        matched.append((row, aid))
    seen = {aid for _, aid in matched}
    # SAJ の順位表は SAJ 登録選手だけを載せる（外国籍選手などは省かれ、後続の順位が詰まる）。
    # そこで順位表に居る選手だけで PDF 側の総合順位を付け直してから比べる。
    present = [ours[aid] for aid in seen if ours[aid]['overall'] is not None]
    # 同順位（決勝の DNF 2 人がともに 11 位など）は同順位のまま付け直す：自分より上の人数 + 1
    rerank = {id(o): 1 + sum(1 for p in present if p['overall'] < o['overall']) for o in present}
    for row, aid in matched:
        o = ours[aid]
        html_rank = int(row['rank']) if str(row.get('rank') or '').strip().isdigit() else None
        html_score = _dec(row.get('score'))
        our_rank = rerank.get(id(o))
        # 順位表が欠番を詰めずに載せている年もある（2020 FIS DM 女子は 25 位が両方に無く、26 位がそのまま）。
        # PDF の総合順位そのものと一致すれば一致とする
        if html_rank is not None and html_rank == o['overall']:
            our_rank = html_rank
        if html_rank != our_rank:
            note = f"（順位表に無い選手を除いた順位。PDF の総合 {o['overall']}位、{o['round']} {o['round_rank']}位）" if our_rank != o['overall'] else f"（{o['round']} {o['round_rank']}位）"
            f.append(Finding('error', round_id, 'layer5', f"{gender} {o['name']}: 順位表 {html_rank}位 / PDF 再構成 {our_rank}位{note}"))
            continue
        if html_score is not None and o['score'] is not None and not _num_eq(html_score, o['score']):
            f.append(Finding('error', round_id, 'layer5', f"{gender} {o['name']}: 順位表の得点 {html_score} / PDF {o['score']}（{o['round']}）"))
            continue
    for aid, o in ours.items():
        if aid not in seen and o['overall'] is not None:
            f.append(Finding('warning', round_id, 'layer5', f"{gender} {o['name']}（PDF {o['overall']}位）が順位表に無い（SAJ 未登録の選手は載らない）"))
    status = 'error' if any(x.level == 'error' for x in f) else 'ok'
    return f, status


def compare_dm(gender, round_id, ours, rows):
    """デュアルモーグル（順位のみ）: 敗退した選手の順位の付け方が PDF と順位表で違う大会がある（順位表は組み合わせの枠どおり
    17 位から、PDF は出場人数で詰める。1 回戦 DNF は PDF に順位があり順位表は空欄）。そこで順位の数字そのものではなく、
    両方に順位のある選手どうしの前後関係と、順位表の選手が PDF に居ることを確かめる"""
    f = []
    by_no = {_norm_no(v['saj_no']): k for k, v in ours.items() if v.get('saj_no')}
    by_name = {_norm_name(v['name']): k for k, v in ours.items()}
    pairs = []
    for row in rows:
        aid = by_no.get(_norm_no(row.get('code'))) or by_name.get(_norm_name(row.get('name')))
        html_rank = int(row['rank']) if str(row.get('rank') or '').strip().isdigit() else None
        if aid is None:
            level = 'error' if html_rank is not None else 'warning'
            f.append(Finding(level, round_id, 'layer5', f"{gender} 順位表の {row.get('rank') or '（順位なし）'}位 {row.get('name')}（{row.get('code')}）が PDF 側に居ない"))
            continue
        o = ours[aid]
        if html_rank is None or o['overall'] is None:
            if html_rank != o['overall']:
                f.append(Finding('warning', round_id, 'layer5', f"{gender} {o['name']}: 順位表 {html_rank or '空欄'} / PDF {o['overall'] or '空欄'}（DM。片方だけ順位が無い）"))
            continue
        pairs.append((o['overall'], html_rank, o['name']))
    pairs.sort()
    for (pa, ha, na), (pb, hb, nb) in zip(pairs, pairs[1:]):
        if pa < pb and ha > hb:
            f.append(Finding('error', round_id, 'layer5', f"{gender} DM の順位の前後が逆: PDF {na} {pa}位・{nb} {pb}位 / 順位表 {ha}位・{hb}位"))
    shifted = sum(1 for p, h, _ in pairs if p != h)
    if shifted:
        f.append(Finding('warning', round_id, 'layer5', f"{gender} DM: 順位の数字が PDF と順位表で違う選手 {shifted} 名（前後関係は一致。敗退者の順位の付け方の違い）"))
    status = 'error' if any(x.level == 'error' for x in f) else 'ok'
    return f, status


def apply_exceptions(findings, ev, round_ids):
    """registry の layer5_exceptions（PDF と順位表の食い違いを確かめたうえで PDF を正とするもの）に当たるエラーを警告に下げる。
    登録したのに当たる食い違いが無ければエラー（登録の見直し）"""
    excs = [e for e in (ev or {}).get('layer5_exceptions') or [] if e.get('round_id') in round_ids]
    if not excs:
        return findings
    out, used = [], set()
    for x in findings:
        if x.level == 'error':
            for i, e in enumerate(excs):
                if i not in used and x.round_id == e.get('round_id') and all(str(s) in x.message for s in e.get('match', [])):
                    x = Finding('warning', x.round_id, 'layer5', f"{x.message}（PDF を正とする例外。根拠: {e['basis']}）")
                    used.add(i)
                    break
        out.append(x)
    for i, e in enumerate(excs):
        if i not in used:
            out.append(Finding('error', e.get('round_id'), 'layer5', f"layer5_exceptions の {e.get('match')} に当たる食い違いが無い（登録を見直す）"))
    return out


def cross_check(rounds_ctx, events_by_id):
    """SAJ 系（page_url が sajdb の順位表）のラウンドについて、キャッシュがあれば照合する。
    戻り値: (findings, {round_id: status})"""
    findings = []
    status = {}
    groups = collections.defaultdict(dict)
    for ctx in rounds_ctx:
        r = ctx['round']
        if 'sajdb.shikuminet.jp' not in (r['source'].get('page_url') or ''):
            continue
        # 年齢区分のある大会（全日本ジュニア）は区分ごとに別のまとまりにする。総合の部（選手の集計に数えない）は照合しない
        if r.get('category') and not any(x.get('counting', True) for x in ctx['runs']):
            status[r['round_id']] = 'skipped'
            continue
        groups[(r['event_id'], r['gender'], r.get('category'))][r['round']] = (r, ctx['runs'])
    by_eg = collections.defaultdict(list)
    for (event_id, gender, cat), rbc in groups.items():
        by_eg[(event_id, gender)].append((cat, rbc))
    for (event_id, gender), cands in by_eg.items():
        page_url = next(iter(cands[0][1].values()))[0]['source']['page_url']
        cache = load_cache(page_url)
        if cache is None:
            for _, rbc in cands:
                for rnd, _ in rbc.values():
                    status[rnd['round_id']] = 'skipped'
            continue
        if len(cands) > 1:
            # 順位表は区分ごとに別ページ（例: 2024 全日本ジュニア 0430 は高校生の部だけ）。SAJ 番号がいちばん多く重なる区分と照合する
            codes = {_norm_no(r.get('code')) for r in cache.get('rows') or []}

            def overlap(rbc):
                return len({_norm_no(x.get('saj_no')) for _, runs in rbc.values() for x in runs} & codes)
            cands = sorted(cands, key=lambda c: -overlap(c[1]))
            for cat, rbc in cands[1:]:
                for rnd, _ in rbc.values():
                    status[rnd['round_id']] = 'skipped'
                findings.append(Finding('warning', next(iter(rbc.values()))[0]['round_id'], 'layer5',
                                        f"{gender} 順位表（{cache_key(page_url)}）は {cands[0][0]} のもの。{cat} は照合していない"))
        rbc = cands[0][1]
        # registry の layer5_skip: 順位表と照合できない理由が分かっているラウンド（例: PDF が予選だけで順位表は決勝後の順位）
        skip = [s for s in (events_by_id.get(event_id) or {}).get('layer5_skip') or []
                if s.get('round_id') in {rnd['round_id'] for rnd, _ in rbc.values()}]
        if skip:
            for rnd, _ in rbc.values():
                status[rnd['round_id']] = 'skipped'
            findings.append(Finding('warning', skip[0]['round_id'], 'layer5', f"{gender} 順位表と照合しない。理由: {skip[0]['basis']}"))
            continue
        f, st = compare(event_id, gender, rbc, cache)
        f = apply_exceptions(f, events_by_id.get(event_id), {rnd['round_id'] for rnd, _ in rbc.values()})
        if st != 'upstream_missing':
            st = 'error' if any(x.level == 'error' for x in f) else 'ok'
        findings += f
        for rnd, _ in rbc.values():
            status[rnd['round_id']] = st
    return findings, status
