"""多層照合 (multi-layer verification).

Each layer returns a list of Finding(level, round_id, layer, message). level is 'error' or 'warning'.
A round with any error is not published (all-or-nothing per event).

Layers apply per tier:
  detail : layer0, layer1 (two readers agree), layer2 (recomputation), layer3, layer4, layer5, golden
  score  : layer0, layer3 (rank order only), layer4, layer5, golden
  rank   : layer0, layer4, layer5, golden
"""
import json, os, collections
from decimal import Decimal
from . import scoring

LAYERS = ['layer0', 'layer1', 'layer2', 'layer3', 'layer4', 'layer5', 'golden']
LAYERS_BY_TIER = {'detail': set(LAYERS), 'score': {'layer0', 'layer3', 'layer4', 'layer5', 'golden'},
                  'rank': {'layer0', 'layer4', 'layer5', 'golden'}}


class Finding:
    def __init__(self, level, round_id, layer, message):
        self.level, self.round_id, self.layer, self.message = level, round_id, layer, message

    def as_dict(self):
        return {'level': self.level, 'round_id': self.round_id, 'layer': self.layer, 'message': self.message}


def _num_eq(a, b, tol=Decimal('0.005')):
    if a is None or b is None:
        return a is None and b is None
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tol


# ---------------------------------------------------------------- layer 0: completeness
def layer0(rounds_ctx, expected, accept_rounds, check_missing=True):
    """rounds_ctx: list of dicts {round, runs, records_a, cls}. expected: dict round_id -> n athletes."""
    f = []
    seen_ids = set()
    for ctx in rounds_ctx:
        r = ctx['round']
        key = r['round_id']
        n_ath = len({x['athlete_id'] for x in ctx['records_a']})
        if r['n_competitors'] is not None and r['n_competitors'] != n_ath:
            f.append(Finding('error', key, 'layer0', f"出走数 {r['n_competitors']} に対し抽出 {n_ath} 名"))
        if n_ath == 0:
            f.append(Finding('error', key, 'layer0', '選手が1人も読めていない'))
        if key not in expected:
            if accept_rounds:
                expected[key] = n_ath
            f.append(Finding('warning', key, 'layer0',
                             f"新しいラウンド（{n_ath} 名）。内容確認後 `--accept-rounds` で基準に登録" if not accept_rounds
                             else f"新しいラウンドを基準に登録（{n_ath} 名）"))
        elif expected[key] != n_ath:
            f.append(Finding('error', key, 'layer0', f"登録済み基準 {expected[key]} 名と一致しない（今回 {n_ath} 名）"))
        for run in ctx['runs']:
            if run['run_id'] in seen_ids:
                f.append(Finding('error', key, 'layer0', f"run_id 重複: {run['run_id']}"))
            seen_ids.add(run['run_id'])
        if r['tier'] == 'detail':
            if r['pace_time'] is None:
                f.append(Finding('error', key, 'layer0', 'ペースタイムが読めない'))
            panel = r.get('panel') or {}
            want = (panel.get('turns') or 0) + (panel.get('air') or 0)
            if want and len(r['judges']) != want:
                f.append(Finding('warning', key, 'layer0', f"審判が {len(r['judges'])} 名（{want} 名想定）"))
    present = {c['round']['round_id'] for c in rounds_ctx}
    for key in (expected if check_missing else []):
        if key not in present:
            f.append(Finding('error', key, 'layer0', '基準に登録済みのラウンドが今回の取り込みに無い（PDF が消えた？）'))
    return f


# ---------------------------------------------------------------- layer 1: two readers agree (FIS-format records)
FIELDS_REC = ['rank', 'bib', 'fis_code', 'name', 'noc', 'yb', 'status', 'reserve_judge', 'seconds', 'time_points',
              'air_total', 'base_total', 'ded_total', 'turns_total', 'run_score', 'tie', 'q_block', 'best_score', 'counting']


def _rec_key(rec):
    return (rec['fis_code'], rec.get('q_block'), bool(rec.get('counting', True)))


def layer1(round_id, recs_a, recs_b, meta_a, meta_b):
    f = []
    if recs_b is None:
        f.append(Finding('error', round_id, 'layer1', 'パーサ B が利用できない'))
        return f
    da = {_rec_key(r): r for r in recs_a}
    db = {_rec_key(r): r for r in recs_b}
    for k in da.keys() - db.keys():
        f.append(Finding('error', round_id, 'layer1', f"B に無い記録: {k}"))
    for k in db.keys() - da.keys():
        f.append(Finding('error', round_id, 'layer1', f"A に無い記録: {k}"))
    for k in da.keys() & db.keys():
        a, b = da[k], db[k]
        for fld in FIELDS_REC:
            va, vb = a.get(fld), b.get(fld)
            same = _num_eq(va, vb) if isinstance(va, (int, float)) and not isinstance(va, bool) and isinstance(vb, (int, float)) else (va == vb)
            if not same:
                f.append(Finding('error', round_id, 'layer1', f"{a['name']} {k[1] or ''} {fld}: A={va} B={vb}"))
        for fld in ('base_scores', 'ded_scores'):
            if [x for x in a.get(fld, [])] != [x for x in b.get(fld, [])]:
                f.append(Finding('error', round_id, 'layer1', f"{a['name']} {fld}: A={a.get(fld)} B={b.get(fld)}"))
        ja, jb = a.get('air_jumps', []), b.get('air_jumps', [])
        if len(ja) != len(jb) or any(x != y for x, y in zip(ja, jb)):
            f.append(Finding('error', round_id, 'layer1', f"{a['name']} air_jumps: A={ja} B={jb}"))
    for fld in ('num_competitors', 'pace_time', 'codex', 'date', 'event'):
        if meta_a.get(fld) != meta_b.get(fld):
            f.append(Finding('error', round_id, 'layer1', f"meta {fld}: A={meta_a.get(fld)} B={meta_b.get(fld)}"))
    ja = [(j['judge_no'], j['name'], j['noc']) for j in meta_a.get('judges', [])]
    jb = [(j['judge_no'], j['name'], j['noc']) for j in meta_b.get('judges', [])]
    if ja != jb:
        f.append(Finding('error', round_id, 'layer1', f"judges: A={ja} B={jb}"))
    return f


# ---------------------------------------------------------------- layer 2: recomputation
def layer2(round_id, runs, dd_table, gender, dd_seen):
    f = []
    for run in runs:
        if run['status'] != 'OK' or '_recomputed' not in run:
            continue
        p, rc = run['_printed'], run['_recomputed']
        for fld, (calc, exc) in rc.get('_exception_applied', {}).items():
            f.append(Finding('warning', round_id, 'layer2',
                             f"{run['name']} {fld}: 印字 {p.get(fld)} / 再計算 {calc} — 規則ファイルの例外（印字を正とする）: {exc.get('basis')}"))
        for fld, val in (('time_points', rc['time_points']), ('air_total', rc['air_total']), ('base_total', rc['base_total']),
                         ('ded_total', rc['ded_total']), ('turns_total', rc['turns_total']), ('run_score', rc['run_score'])):
            if val is None or p.get(fld) is None:
                continue  # not printed on this format (SAJ prints no base/ded totals)
            if not _num_eq(p.get(fld), val):
                f.append(Finding('error', round_id, 'layer2', f"{run['name']} {run.get('q_block') or ''} {fld}: 印字 {p.get(fld)} / 再計算 {val}"))
        for j in run['air']:
            dd_seen[(run['season'], run['series'], gender, j['jump'])].add(j['dd'])
            if dd_table and j['jump'] in dd_table.get(gender, {}):
                if not _num_eq(j['dd'], dd_table[gender][j['jump']]):
                    f.append(Finding('warning', round_id, 'layer2',
                                     f"{run['name']} ジャンプ {j['jump']} の DD {j['dd']} が DD 表 {dd_table[gender][j['jump']]} と違う（同カテゴリ2本の低い方適用 ICR 4210.2.2 の可能性）"))
    return f


def dd_consistency(dd_seen):
    f = []
    for (season, series, gender, code), dds in sorted(dd_seen.items()):
        if len(dds) > 1:
            f.append(Finding('warning', f"{season}", 'layer2', f"{series} {gender} ジャンプ {code} の DD がシーズン内で複数 {sorted(dds)}（規則の調整か、誤読）"))
    return f


# ---------------------------------------------------------------- layer 3: ranks & progression
def _athlete_items(runs):
    by = collections.OrderedDict()
    for r in runs:
        by.setdefault(r['athlete_id'], []).append(r)
    items = []
    for code, blocks in by.items():
        ok = [b for b in blocks if b['status'] == 'OK' and b['run_score'] is not None]
        best = max(ok, key=lambda b: Decimal(str(b['run_score']))) if ok else None
        direct = all(b['q_block'] == 'Q1' for b in blocks) and any(b['q_block'] for b in blocks)
        items.append({'athlete_id': code, 'name': blocks[0]['name'], 'rank': blocks[0]['rank'], 'direct': direct,
                      'best_run': best, 'blocks': blocks, 'best_score': blocks[0].get('best_score')})
    return items


def _rank_items(items, rules):
    recs = []
    for it in items:
        b = it['best_run']
        if '_recomputed' in b:
            rc = b['_recomputed']
            recs.append({'item': it, 'run_score': rc['run_score'], 'turns_total': rc['turns_total'],
                         'air_without_dd': rc['air_without_dd'], 'seconds': Decimal(str(b['seconds']))})
        else:  # score tier: only the printed score is available
            recs.append({'item': it, 'run_score': Decimal(str(b['run_score'])),
                         'turns_total': Decimal(str(b['turns_total'] or 0)), 'air_without_dd': Decimal(0),
                         'seconds': Decimal(str(b['seconds'] or 0))})
    return scoring.rank_order(recs, rules)


def layer3_rank(round_id, runs, rules):
    f = []
    items = [it for it in _athlete_items(runs) if it['best_run'] is not None]
    if not items:
        return f
    q_layout = any(r['q_block'] for r in runs)
    if q_layout:
        for it in items:
            best = Decimal(str(it['best_run']['run_score']))
            if it['best_score'] is None or not _num_eq(it['best_score'], best):
                f.append(Finding('error', round_id, 'layer3', f"{it['name']} 採用点 {it['best_score']} がブロック最高点 {best} と違う"))
    groups = [[it for it in items if it['direct']], [it for it in items if not it['direct']]] if q_layout else [items]
    # SAJ 国内大会: 同点欄（T1/T2）が印字されたラウンドだけタイブレークで順位を分け、それ以外は同点＝同順位。
    rules_eff = rules
    if rules.get('tie_break_if_marked') and any(r.get('tie') for r in runs):
        rules_eff = dict(rules, tie_break=rules['tie_break_if_marked'])
    offset = 0
    for grp in groups:
        if not grp:
            continue
        for rec, rank in _rank_items(grp, rules_eff):
            it = rec['item']
            if it['rank'] != rank + offset:
                f.append(Finding('error', round_id, 'layer3', f"{it['name']} 順位 印字 {it['rank']} / 再構成 {rank + offset}"))
        offset += len(grp)
    return f


def _cut(items_ranked, n):
    ranked = sorted([it for it in items_ranked if it['rank']], key=lambda it: it['rank'])
    if not ranked:
        return []
    if len(ranked) >= n:
        nth = ranked[n - 1]['rank']
        return [it for it in ranked if it['rank'] <= nth]
    return ranked


def layer3_progression(event_id, rounds_by_code, advance):
    """rounds_by_code: {code: (round, runs)}; advance: {from_code: {'to': code, 'n': int, ...}}."""
    f = []
    adv = advance
    codes = rounds_by_code
    items = {code: _athlete_items(runs) for code, (rnd, runs) in codes.items()}

    def expected_for(to):
        if to == 'F1':
            n1 = adv.get('Q1', {}).get('n', 0)
            n2 = adv.get('Q2', {}).get('n', 0)
            if 'Q2' in codes:
                q2 = items['Q2']
                if any(it['direct'] for it in q2):
                    return {it['athlete_id'] for it in _cut(q2, n1 + n2)}, ['Q2'], f"Q2 報告の上位 {n1 + n2}"
                exp = {it['athlete_id'] for it in _cut(q2, n2)}
                srcs = ['Q2']
                if 'Q1' in codes:
                    exp |= {it['athlete_id'] for it in _cut(items['Q1'], n1)}
                    srcs.append('Q1')
                return exp, srcs, f"Q1 上位 {n1} ＋ Q2 上位 {n2}"
            if 'Q' in codes:
                n = adv.get('Q', {}).get('n') or (n1 + n2)
                return {it['athlete_id'] for it in _cut(items['Q'], n)}, ['Q'], f"Q 上位 {n}"
            if 'Q1' in codes:
                return {it['athlete_id'] for it in _cut(items['Q1'], n1)}, ['Q1'], f"Q1 上位 {n1}"
            return None, [], ''
        prev = {'F2': 'F1', 'F3': 'F2'}[to]
        if prev not in codes:
            return None, [], ''
        n = adv.get(prev, {}).get('n')
        if not n:
            return None, [], ''
        return {it['athlete_id'] for it in _cut(items[prev], n)}, [prev], f"{prev} 上位 {n}"

    for to in ('F1', 'F2', 'F3'):
        if to not in codes:
            continue
        exp, srcs, desc = expected_for(to)
        if exp is None:
            continue
        rnd_to, runs_to = codes[to]
        present = {it['athlete_id']: it for it in items[to]}
        in_sources = {it['athlete_id']: (code, it) for code in srcs for it in items[code]}
        missing = [c for c in exp if c not in present]
        for c in missing:
            src_code, it = in_sources[c]
            f.append(Finding('warning', rnd_to['round_id'], 'layer3', f"{src_code} {it['rank']}位 {it['name']} が {to} に居ない（欠場？）"))
        for c, it in present.items():
            if c in exp:
                continue
            if c in in_sources:
                src_code, src = in_sources[c]
                f.append(Finding('error', rnd_to['round_id'], 'layer3', f"{to} の {it['name']} は {src_code} {src['rank']}位で通過枠（{desc}）の外"))
            else:
                f.append(Finding('warning', rnd_to['round_id'], 'layer3', f"{to} の {it['name']} は {'/'.join(srcs)} に出走していない（シード直接進出？）"))
        if len(present) != len(exp) and not missing:
            f.append(Finding('warning', rnd_to['round_id'], 'layer3', f"{to} の人数 {len(present)} が通過枠 {len(exp)}（{desc}）と違う"))
    return f


# ---------------------------------------------------------------- layer 4: cross-file consistency
def layer4(all_runs, rounds):
    f = []
    by_id = collections.defaultdict(list)
    for r in all_runs:
        by_id[r['athlete_id']].append(r)
    for aid, rs in by_id.items():
        ybs = {r['yb'] for r in rs if r['yb']}
        if len(ybs) > 1:
            f.append(Finding('error', 'global', 'layer4', f"選手 {aid} の生年が複数 {sorted(ybs)}: {sorted({r['name'] for r in rs})}"))
        names = {r['name'] for r in rs}
        if len(names) > 1:
            f.append(Finding('warning', 'global', 'layer4', f"選手 {aid} の氏名表記が複数 {sorted(names)}（別名として扱う）"))
        nocs = {r['noc'] for r in rs if r['noc']}
        if len(nocs) > 1:
            f.append(Finding('warning', 'global', 'layer4', f"選手 {aid} {sorted(names)[0]} の国が複数 {sorted(nocs)}（履歴として扱う）"))
        sajs = {r['saj_no'] for r in rs if r.get('saj_no')}
        if len(sajs) > 1:
            # 外国籍選手は SAJ 番号欄に FIS コードが印字される年がある（全日本2024 MOON SEOYOUNG）。その場合は警告に留める。
            level = 'warning' if any(s == aid for s in sajs) else 'error'
            f.append(Finding(level, 'global', 'layer4', f"選手 {aid} {sorted(names)[0]} の SAJ 番号が複数 {sorted(sajs)}"))
        fiss = {r['fis_code'] for r in rs if r.get('fis_code')}
        if len(fiss) > 1:
            f.append(Finding('error', 'global', 'layer4', f"選手 {aid} {sorted(names)[0]} の FIS コードが複数 {sorted(fiss)}"))
    by_name = collections.defaultdict(set)
    for r in all_runs:
        by_name[(r['name'], r['yb'])].add(r['athlete_id'])
    for (name, yb), ids in by_name.items():
        if len(ids) > 1:
            f.append(Finding('warning', 'global', 'layer4', f"同じ氏名 {name} {yb or ''} に選手 ID が複数 {sorted(ids)}（別人か確認）"))
    by_event = collections.defaultdict(list)
    for r in rounds:
        by_event[(r['event_id'], r['gender'])].append(r)
    for key, rs in by_event.items():
        paces = {r['pace_time'] for r in rs}
        if len(paces) > 1:
            f.append(Finding('warning', rs[0]['event_id'], 'layer4', f"{key[1]} ラウンド間でペースタイムが違う {sorted(str(p) for p in paces)}"))
        panels = {tuple((j['no'], j['name']) for j in r['judges']) for r in rs}
        if len(panels) > 1:
            f.append(Finding('warning', rs[0]['event_id'], 'layer4', f"{key[1]} ラウンド間で審判構成が違う"))
    return f


# ---------------------------------------------------------------- golden dataset
def golden(golden_dir, runs_by_id, strict=True):
    f = []
    rounds_present = {r['round_id'] for r in runs_by_id.values()}
    if not os.path.isdir(golden_dir):
        return f, 0
    n = 0
    for fn in sorted(os.listdir(golden_dir)):
        if not fn.endswith('.json'):
            continue
        with open(os.path.join(golden_dir, fn), encoding='utf-8') as fh:
            g = json.load(fh)
        by_round_bib = {}
        for r in runs_by_id.values():
            by_round_bib[(r['round_id'], r.get('bib'))] = r
        for item in g.get('runs', []):
            n += 1
            if item.get('run_id'):
                ident = item['run_id']
                run = runs_by_id.get(ident)
                in_scope = any(ident.startswith(r + '-') for r in rounds_present)
            else:  # round_id + bib（SAJ 様式は選手 ID が印字にないので BIB で特定する）
                ident = f"{item['round_id']} BIB {item['bib']}"
                run = by_round_bib.get((item['round_id'], item['bib']))
                in_scope = item['round_id'] in rounds_present
            if run is None and not strict and not in_scope:
                n -= 1
                continue
            round_ref = item.get('round_id') or item['run_id'].rsplit('-', 1)[0]
            if run is None:
                f.append(Finding('error', round_ref, 'golden', f"{ident}: 正解データのランが出力に無い ({fn})"))
                continue
            for k, v in item.items():
                if k in ('run_id', 'round_id', 'bib', 'note', 'source'):
                    continue
                actual = run.get(k)
                if k in ('base', 'ded'):
                    same = [float(x) for x in v] == [float(x) for x in actual]
                elif k == 'air':
                    # 正解データは1本目だけ・2本目だけを記録していることがある（画像で読めた分）。
                    # 記録されたジャンプがすべて出力に含まれ、出力のジャンプ数を超えないことを確かめる。
                    out_air = [(a['J6'], a['J7'], a['jump'], a['dd']) for a in actual]
                    same = len(v) <= len(out_air) and all((a['J6'], a['J7'], a['jump'], a['dd']) in out_air for a in v)
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    same = _num_eq(v, actual)
                else:
                    same = v == actual
                if not same:
                    f.append(Finding('error', round_ref, 'golden', f"{ident} {k}: 正解 {v} / 出力 {actual} ({fn})"))
    return f, n
