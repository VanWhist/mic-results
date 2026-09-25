"""第5層（SAJ 系）: SAJ 競技データバンクの順位表 HTML との外部照合。

順位表ページ（registry の pdfs[].page_url、例 https://sajdb.shikuminet.jp/freestyle/2026/competition/0443/result）には
選手ごとに「最終順位」「SAJ 番号」「氏名」「最後に滑ったラウンドの得点」が載る。ブラウザで人の速度で開いて表を
JSON に写したもの（etl/layer5_cache/saj/<seasoncode>_<codex>.json、{'url','rows':[{'rank','bib','code','name','club','pref','score'}]}）
と、PDF 側から再構成した総合順位（決勝→準決勝→予選の順に並べる）を突き合わせる。

注意: PDF も順位表も同じ SAJ の結果システム由来で、独立した第三者ソースではない。「自分が正しく転記したか」の確認として使う。
"""
import collections, os, re, json
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
        for rank_in_round, r in ranked_items:
            placed += 1
            out[r['athlete_id']] = {'overall': placed, 'round': code, 'round_rank': rank_in_round,
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
    return ''.join((s or '').split())


def _dec(s):
    """順位表の得点欄 → Decimal。空欄・'-'・'DNF' などは None"""
    try:
        return Decimal(str(s).strip())
    except Exception:  # noqa
        return None


def compare(event_id, gender, rounds_by_code, cache):
    """戻り値: (findings, status)。status は 'ok' | 'error' | 'upstream_missing'"""
    f = []
    rows = cache.get('rows') or []
    if not rows:
        return f, 'upstream_missing'
    ours = overall_from_rounds(rounds_by_code)
    round_id = rounds_by_code[[c for c in ROUND_ORDER_DESC if c in rounds_by_code][0]][0]['round_id']
    by_no = {_norm_no(v['saj_no']): k for k, v in ours.items() if v.get('saj_no')}
    by_name = {_norm_name(v['name']): k for k, v in ours.items()}
    matched = []
    for row in rows:
        aid = by_no.get(_norm_no(row.get('code'))) or by_name.get(_norm_name(row.get('name')))
        if aid is None:
            f.append(Finding('error', round_id, 'layer5', f"{gender} 順位表の {row.get('rank')}位 {row.get('name')}（{row.get('code')}）が PDF 側に居ない"))
            continue
        matched.append((row, aid))
    seen = {aid for _, aid in matched}
    # SAJ の順位表は SAJ 登録選手だけを載せる（外国籍選手などは省かれ、後続の順位が詰まる）。
    # そこで順位表に居る選手だけで PDF 側の総合順位を付け直してから比べる。
    present = sorted([ours[aid] for aid in seen if ours[aid]['overall'] is not None], key=lambda o: o['overall'])
    rerank = {}
    for i, o in enumerate(present, start=1):
        rerank[id(o)] = i
    for row, aid in matched:
        o = ours[aid]
        html_rank = int(row['rank']) if str(row.get('rank') or '').strip().isdigit() else None
        html_score = _dec(row.get('score'))
        our_rank = rerank.get(id(o))
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
        groups[(r['event_id'], r['gender'])][r['round']] = (r, ctx['runs'])
    for (event_id, gender), rbc in groups.items():
        page_url = next(iter(rbc.values()))[0]['source']['page_url']
        cache = load_cache(page_url)
        rids = [rnd['round_id'] for rnd, _ in rbc.values()]
        if cache is None:
            for rid in rids:
                status[rid] = 'skipped'
            continue
        f, st = compare(event_id, gender, rbc, cache)
        findings += f
        for rid in rids:
            status[rid] = st
    return findings, status
