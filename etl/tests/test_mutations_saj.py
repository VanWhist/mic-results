"""変異テスト（SAJ 様式）: 正しいデータをわざと壊し、多層照合のどの層が止めるかを確かめる。

    python -m etl.tests.test_mutations_saj

各変異について「少なくとも1つの層がエラーを出す」ことを要求する。第1層は、変異前の座標読み取り（B）を
相手にして、変異させた行読み取り（A）と比べる。確かめているのは検証ロジックであって、
2方式の独立性ではない（それは本番ビルドで確かめる）。
"""
import os, sys, copy, collections
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from etl import config, verify, normalize
from etl.adapters.saj_aj import adapter, parse_sajmo, verify_nc

PDF = os.path.join(config.PDF_ROOT, 'SAJ_AJ', '2026', '全日本2026_MO.pdf')
RULES, RAW = adapter.load_event_rules('規則_2026')
ADV = {'Q': {'to': 'F1', 'n': 16}, 'F1': {'to': 'F2', 'n': 6}}


def load():
    meta, sections = parse_sajmo.parse_pdf(PDF)
    adapter.section_codes(sections)
    _, _, tables = verify_nc.read_pdf(PDF)
    rounds_b, _ = verify_nc.group_rounds(tables)
    return meta, sections, rounds_b


def run_layers(meta, sections, rounds_b, expected=None):
    findings = []
    ctxs = []
    dd_seen = collections.defaultdict(set)
    for s in sections:
        nturn = s.get('nturn', 5)
        rules = RULES[nturn]
        code = adapter.SAJ_CODE[s['saj_code']]
        g = adapter.GENDER[s['gender']]
        b_round = adapter.match_b_round(rounds_b, s['gender'], len(s['athletes']))
        cls = {'event_id': 'test-event', 'season': '2025-26', 'series': 'SAJ_AJ', 'grade': '全日本', 'discipline': 'MO',
               'gender': g, 'round': code, 'round_text': s['round'], 'codex': None, 'tier': 'detail',
               'panel': {'turns': nturn, 'air': 2}, 'rel': 'x', 'path': PDF, 'pdf_sha256': None, 'pages': s['pages'], 'rules_version': '規則_2026'}
        pace = b_round['pace'] if b_round else None
        rmeta = {'date': meta.get('date'), 'judges': [], 'pace_time': float(pace) if pace is not None else None, 'num_competitors': None}
        records = [adapter.to_record(a, nturn) for a in s['athletes']]
        rnd, runs = normalize.make_round(cls, rmeta, records, rules, 'test')
        round_id = rnd['round_id']
        if b_round is None:
            findings.append(verify.Finding('error', round_id, 'layer1', 'B round not found'))
        else:
            findings += adapter.layer1_tokens(round_id, s, b_round['blocks'])
        findings += verify.layer2(round_id, runs, None, g, dd_seen)
        findings += verify.layer3_rank(round_id, runs, rules)
        ctxs.append({'cls': cls, 'round': rnd, 'runs': runs, 'records_a': records, 'rules': rules})
    exp = expected if expected is not None else {c['round']['round_id']: len({x['athlete_id'] for x in c['records_a']}) for c in ctxs}
    findings += verify.layer0(ctxs, exp, False)
    rbc = {c['round']['round']: (c['round'], c['runs']) for c in ctxs}
    findings += verify.layer3_progression('test-event', rbc, ADV)
    findings += verify.layer4([r for c in ctxs for r in c['runs']], [c['round'] for c in ctxs])
    errs = collections.defaultdict(list)
    for f in findings:
        if f.level == 'error':
            errs[f.layer].append(f.message)
    return errs


def main():
    meta, sections, rounds_b = load()
    base = run_layers(meta, sections, rounds_b)
    print('変異なし:', dict((k, len(v)) for k, v in base.items()) or 'エラー 0')
    assert not base, '変異なしでエラーがある'
    q = next(s for s in sections if s['saj_code'] == 'Q')
    f1 = next(s for s in sections if s['saj_code'] == 'F')
    expected = {f"test-event-{adapter.GENDER[s['gender']]}-{adapter.SAJ_CODE[s['saj_code']]}": len(s['athletes']) for s in (q, f1)}
    ok_rows = [a for a in q['athletes'] if a['status'] == 'OK']

    mutations = []

    def mut(name, fn):
        mutations.append((name, fn))

    mut('ベース点 J1/J2 入れ替え', lambda S: S[0]['athletes'].__setitem__(0, dict(ok_rows[0], base=[ok_rows[0]['base'][1], ok_rows[0]['base'][0]] + ok_rows[0]['base'][2:])))
    mut('ベース点を 0.1 変える', lambda S: S[0]['athletes'][0].__setitem__('base', [ok_rows[0]['base'][0] + 0.1] + ok_rows[0]['base'][1:]))
    mut('減点を 0.1 変える', lambda S: S[0]['athletes'][0].__setitem__('ded', [ok_rows[0]['ded'][0] + 0.1] + ok_rows[0]['ded'][1:]))
    mut('DD を変える', lambda S: S[0]['athletes'][0].__setitem__('dd1', ok_rows[0]['dd1'] + 0.05))
    mut('技コードを変える', lambda S: S[0]['athletes'][0].__setitem__('jump1', 'XX'))
    mut('タイムを変える', lambda S: S[0]['athletes'][0].__setitem__('time', ok_rows[0]['time'] + 0.5))
    mut('スコアを変える', lambda S: S[0]['athletes'][0].__setitem__('score', ok_rows[0]['score'] + 1))
    mut('順位を入れ替える', lambda S: (S[0]['athletes'][0].__setitem__('rank', 2), S[0]['athletes'][1].__setitem__('rank', 1)))
    mut('1人消す', lambda S: S[0]['athletes'].pop(5))
    mut('1人複製する', lambda S: S[0]['athletes'].append(copy.deepcopy(S[0]['athletes'][3])))
    mut('FIS コードを変える', lambda S: S[0]['athletes'][0].__setitem__('fisno', '2599999'))
    mut('決勝に予選外の選手を入れる', lambda S: S[1]['athletes'].__setitem__(0, dict(S[1]['athletes'][0], fisno=q['athletes'][-1]['fisno'], sajno=q['athletes'][-1]['sajno'], name=q['athletes'][-1]['name'])))
    mut('減点の符号を落とす（減点なし扱い）', lambda S: S[0]['athletes'][0].__setitem__('ded', None))

    n_fail = 0
    for name, fn in mutations:
        S = [copy.deepcopy(q), copy.deepcopy(f1)]
        try:
            fn(S)
        except Exception as e:  # noqa
            print(f"  {name}: 変異を適用できない {e!r}")
            n_fail += 1
            continue
        errs = run_layers(meta, S, rounds_b, expected)
        caught = sorted(errs.keys())
        status = 'OK' if caught else '!! 通過'
        if not caught:
            n_fail += 1
        print(f"  {status:8s} {name}: 止めた層 {caught}")
    print(f"\n{len(mutations)} 変異 / 通過 {n_fail}")
    return 1 if n_fail else 0


if __name__ == '__main__':
    sys.exit(main())
