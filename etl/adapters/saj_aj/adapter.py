# -*- coding: utf-8 -*-
"""SAJ 国内リザルト（SAJ03/07-FM 様式）の取り込みアダプタ。

全日本選手権 10 年分（2017〜2026）で確立した parse_sajmo（文字行の読み取り＝A）と
verify_nc.read_pdf（座標ベースの読み取り＝B）を使い、ETL 共通の round/record 形式にそろえる。
A級・B級・全日本ジュニアも同じ様式（2026-09-25 に4件で確認）なので、このアダプタで扱う。

規則は大会ごとに ``etl/rules/events/規則_YYYY.json``（turns/air/time/pace_by_sheet/recompute_exceptions）で確定する。
"""
import os, re, collections
from decimal import Decimal

from ... import config
from ...verify import Finding
from . import parse_sajmo, verify_nc

PARSER_VERSION = 'saj_aj-1.0 (parse_sajmo 2026-09-17 + verify_nc)'
NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
GENDER = {'男子': 'M', '女子': 'W'}
SAJ_CODE = {'Q': 'Q', 'F': 'F1', 'SF': 'F2'}          # 印字のラウンド記号 → 内部コード
ROUND_TEXT = {'予選': '予選', '決勝': '決勝', '準決勝': '準決勝', 'スーパーファイナル': 'スーパーファイナル'}
PREF_RE = re.compile(r'^(.*?\((?:[^()]*?(?:都|道|府|県|学連|[A-Z]{3}))\))')


def load_event_rules(name):
    """規則_YYYY.json → ETL 共通の rules dict（ジャッジ人数ごと）。"""
    path = os.path.join(config.EVENT_RULES_DIR, name + '.json')
    with open(path, encoding='utf-8') as fh:
        import json
        raw = json.load(fh)
    out = {}
    for nturn, t in raw['turns'].items():
        out[int(nturn)] = {
            'judges': {'turns': int(nturn), 'air': 2}, 'discard_high_low': bool(t['trim']),
            'truncate_decimals': 2, 'air_cap_per_judge': float(raw['air']['cap']), 'turns_floor': float(t['floor']),
            'turns_min_per_judge': 0.1, 'time_a': float(raw['time']['a']), 'time_b': float(raw['time']['b']),
            'time_formula': f"{raw['time']['a']} - {raw['time']['b']} * seconds / pace_time",
            'time_max': float(raw['time']['max']), 'time_min': 0.0,
            # 国内: 同点は同順位。同点欄（T1/T2）が印字された年だけ FIS 式のタイブレークで順位を分ける（2025 予選 7・8 位）。
            'tie_break': [], 'tie_break_if_marked': ['turns_total', 'air_without_dd', 'seconds_asc'], 'dd_table': None,
            'source': raw.get('basis'),
        }
    return out, raw


def clean_judge(name):
    """'橋場 一枝 (北海道) 2nd Air AP 196.0 m' → ('橋場 一枝', '北海道')"""
    m = re.match(r'^(.+?)\s*\(([^)]*)\)', name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return name.strip(), None


def section_codes(sections):
    """性別ごとに、印字の見出し語と人数からラウンド記号（Q/F/SF）を決める。
    予選→Q、スーパーファイナル→SF、準決勝→F、決勝→F（同じ性別に準決勝があれば SF）。人数は Q > F > SF であること。"""
    problems = []
    by_g = collections.defaultdict(list)
    for s in sections:
        by_g[s['gender']].append(s)
    for g, secs in by_g.items():
        heads = {s['round'] for s in secs}
        for s in secs:
            r = s['round']
            if r == '予選':
                s['saj_code'] = 'Q'
            elif r == 'スーパーファイナル':
                s['saj_code'] = 'SF'
            elif r == '準決勝':
                s['saj_code'] = 'F'
            elif r == '決勝':
                s['saj_code'] = 'SF' if '準決勝' in heads else 'F'
            else:
                s['saj_code'] = None
                problems.append(f"{g}: 見出し語 {r!r} からラウンドを決められない")
        order = {'Q': 0, 'F': 1, 'SF': 2}
        seq = sorted([s for s in secs if s['saj_code']], key=lambda s: order[s['saj_code']])
        counts = [len(s['athletes']) for s in seq]
        if counts != sorted(counts, reverse=True) or len({s['saj_code'] for s in seq}) != len(seq):
            problems.append(f"{g}: ラウンドの人数順が Q>F>SF になっていない {[(s['saj_code'], len(s['athletes'])) for s in seq]}")
    return problems


def athlete_id_of(a):
    if a.get('fisno'):
        return str(a['fisno'])
    if a.get('sajno'):
        return 'saj-' + str(a['sajno'])
    return 'x-' + config.slug(a.get('name', '')) + '-' + config.slug(a.get('pref', ''))


def to_record(a, nturn):
    """parse_sajmo の選手 dict → ETL 共通 record。減点は負の数にする。"""
    rec = {
        'rank': a.get('rank'), 'bib': a.get('bib'), 'saj_no': a.get('sajno'), 'fis_code': a.get('fisno'),
        'athlete_id': athlete_id_of(a), 'name': a.get('name'), 'noc': None, 'yb': None,
        'affiliation': a.get('pref'), 'club': a.get('club'),
        'status': a.get('status') or 'OK', 'reserve_judge': False, 'counting': True, 'q_block': None, 'best_score': None,
        'seconds': a.get('time'), 'time_points': a.get('time_point'), 'air_jumps': [], 'air_total': a.get('air_total'),
        'base_scores': list(a.get('base') or []), 'ded_scores': [], 'base_total': None, 'ded_total': None,
        'turns_total': a.get('turns_total'), 'run_score': a.get('score'), 'tie': a.get('tie'), 'page': a.get('page'),
    }
    if rec['status'] == 'OK':
        ded = a.get('ded')
        if ded is None:
            rec['_no_deductions'] = True
            ded = [0.0] * nturn
        rec['ded_scores'] = [-float(d) for d in ded]
        if a.get('jump1') is not None:
            rec['air_jumps'].append({'J6': a['j6_1'], 'J7': a['j7_1'], 'jump': a['jump1'], 'DD': a['dd1']})
        if a.get('jump2') is not None:
            rec['air_jumps'].append({'J6': a['j6_2'], 'J7': a['j7_2'], 'jump': a['jump2'], 'DD': a['dd2']})
    return rec


def printed_sequence_a(a):
    """A 側の記録から、印字順に並んだ数値の列を作る（B の数値トークン列と順序まで突き合わせる）。
    1行目: 順位 BIB SAJ番号 … J1..Jn ターン計 技1 DD1 Ja Jb エア計 タイム タイム点 スコア [同点]
    2行目: FIS番号 … D1..Dn 技2 DD2 Ja Jb
    順序まで比べるのは、J1/J2 の入れ替えのような「合計も多重集合も変わらない」誤りを止めるため。"""
    seq = []

    def add(v):
        if v is not None:
            seq.append(Decimal(str(v)).normalize())
    if a.get('status') != 'OK' or a.get('base') is None:
        add(a.get('rank')); add(a.get('bib'))
        if a.get('sajno') and NUM.match(str(a['sajno'])):
            add(a['sajno'])
        for x in a.get('raw_partial') or []:
            add(x)
        if a.get('fisno'):
            add(a['fisno'])
        return seq
    add(a.get('rank')); add(a.get('bib'))
    if a.get('sajno') and NUM.match(str(a['sajno'])):
        add(a['sajno'])
    for x in a.get('base') or []:
        add(x)
    add(a.get('turns_total'))
    if a.get('jump1') is not None and NUM.match(str(a['jump1'])):
        add(a['jump1'])
    for k in ('dd1', 'j6_1', 'j7_1', 'air_total', 'time', 'time_point', 'score'):
        add(a.get(k))
    if a.get('tie') is not None and NUM.match(str(a['tie'])):
        add(a['tie'])
    if a.get('fisno'):
        add(a['fisno'])
    for x in a.get('ded') or []:
        add(x)
    if a.get('jump2') is not None and NUM.match(str(a['jump2'])):
        add(a['jump2'])
    for k in ('dd2', 'j6_2', 'j7_2'):
        add(a.get(k))
    return seq


def layer1_tokens(round_id, section, blocks_b):
    """読み取り A（行）と B（座標ブロック）の一致。BIB ごとに数値の多重集合と技コードを比べる。"""
    f = []
    by_bib_b = collections.defaultdict(list)
    for b in blocks_b:
        by_bib_b[int(b['bib'])].append(b)
    seen = set()
    for a in section['athletes']:
        bib = a.get('bib')
        if bib is None:
            f.append(Finding('error', round_id, 'layer1', f"A: BIB の無い行 {a.get('name')}"))
            continue
        if bib in seen:
            f.append(Finding('error', round_id, 'layer1', f"A: BIB {bib} が重複"))
        seen.add(bib)
        bl = by_bib_b.get(bib)
        if not bl:
            f.append(Finding('error', round_id, 'layer1', f"B に無い記録: BIB {bib} {a.get('name')}"))
            continue
        if len(bl) > 1:
            f.append(Finding('error', round_id, 'layer1', f"B: BIB {bib} のブロックが {len(bl)} 個"))
            continue
        toks = bl[0]['tokens']
        sb = [Decimal(t).normalize() for t in toks if NUM.match(t)]
        sa = printed_sequence_a(a)
        if sa != sb:
            ca, cb = collections.Counter(sa), collections.Counter(sb)
            if ca == cb:
                f.append(Finding('error', round_id, 'layer1',
                                 f"BIB {bib} {a.get('name')}: 数値の並び順が A/B で違う（A {[str(x) for x in sa]} / B {[str(x) for x in sb]}）"))
            else:
                missing = ca - cb
                extra = cb - ca
                f.append(Finding('error', round_id, 'layer1',
                                 f"BIB {bib} {a.get('name')}: 数値が A/B で違う（A のみ {dict(missing)} / B のみ {dict(extra)}）"))
        for k in ('jump1', 'jump2'):
            j = a.get(k)
            if j is not None and not NUM.match(str(j)) and j not in toks:
                f.append(Finding('error', round_id, 'layer1', f"BIB {bib} {a.get('name')}: 技コード {j} が B に無い"))
        if a.get('name') and not all(part in toks for part in a['name'].split()):
            f.append(Finding('warning', round_id, 'layer1', f"BIB {bib}: 氏名 {a['name']!r} の語が B に無い"))
    for bib in by_bib_b.keys() - seen:
        f.append(Finding('error', round_id, 'layer1', f"A に無い記録: BIB {bib}"))
    return f


def match_b_round(rounds_b, gender_ja, n):
    """B 側のラウンド（性別・人数）を A のセクションに対応づける。"""
    cands = [r for r in rounds_b if r['gender'] == gender_ja and len(r['blocks']) == n]
    return cands[0] if len(cands) == 1 else None


def load_event(ev, imported_at, log=print):
    """registry の 1 大会 → [ctx]。ctx = {cls, meta, records, findings, ab_compared, rules}"""
    rules_by_n, raw_rules = load_event_rules(ev['rules'])
    # sheet 名（pace_by_sheet / recompute_exceptions のキー）: 全日本は "<年>_<Q|F|SF>-<m|w>"、汎用規則の大会は "<event_id>_…"
    sheet_prefix = ev.get('sheet_prefix') or str(raw_rules.get('year'))
    overrides = dict(raw_rules.get('pace_by_sheet', {}))
    overrides.update(ev.get('pace_by_sheet', {}))
    exceptions = list(raw_rules.get('recompute_exceptions', [])) + list(ev.get('recompute_exceptions', []))
    ctxs = []
    for pdf in ev['pdfs']:
        path = os.path.join(config.PDF_ROOT, pdf['path'])
        meta, sections = parse_sajmo.parse_pdf(path)
        problems = section_codes(sections)
        _, pages_b, tables_b = verify_nc.read_pdf(path)
        rounds_b, problems_b = verify_nc.group_rounds(tables_b)
        judges = []
        for jno, (role, name) in sorted(meta['judges'].items()):
            nm, pref = clean_judge(name)
            judges.append({'judge_no': int(jno[1:]), 'role': role, 'name': nm, 'noc': pref})
        for s in sections:
            if not s.get('saj_code'):
                continue
            g = GENDER.get(s['gender'])
            if g is None:
                problems.append(f"性別が読めないセクション {s['heading']!r}")
                continue
            nturn = s.get('nturn', 5)
            rules = rules_by_n.get(nturn)
            code = SAJ_CODE[s['saj_code']]
            sheet = f"{sheet_prefix}_{s['saj_code']}-{'m' if g == 'M' else 'w'}"
            cls = {
                'event_id': ev['event_id'], 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
                'discipline': ev.get('discipline', 'MO'), 'gender': g, 'round': code, 'round_text': s['round'],
                'codex': meta.get('codex'), 'tier': ev.get('tier', 'detail'),
                'panel': {'turns': nturn, 'air': 2, 'air_judge_nos': [nturn + 1, nturn + 2]},
                'rel': pdf['path'], 'path': path, 'pdf_sha256': pdf.get('sha256'), 'url': pdf.get('url'),
                'page_url': (pdf.get('page_urls') or {}).get(g) or pdf.get('page_url'),
                'pages': s['pages'], 'name_ja': ev.get('name_ja'), 'format': ev.get('format'), 'rules_version': ev['rules'],
                'sheet': sheet,
            }
            b_round = match_b_round(rounds_b, s['gender'], len(s['athletes']))
            pace = None
            pace_note = None
            if sheet in overrides:
                pace = Decimal(str(overrides[sheet]['pace']))
                pace_note = overrides[sheet]['basis']
            elif b_round is not None and b_round.get('pace') is not None:
                pace = Decimal(str(b_round['pace']))
            rmeta = {'date': meta.get('date'), 'date_text': meta.get('date'), 'venue': meta.get('venue'),
                     'judges': judges, 'pace_time': float(pace) if pace is not None else None, 'pace_note': pace_note,
                     'num_competitors': None, 'parser_version': PARSER_VERSION, 'title': meta.get('title'),
                     'officials': []}
            records = [to_record(a, nturn) for a in s['athletes']]
            for rec in records:
                exc = {}
                for e in exceptions:
                    if e.get('sheet') == sheet and int(e.get('bib', -1)) == (rec.get('bib') or -2):
                        fld = {'Turns Total': 'turns_total', 'Air Total': 'air_total', 'Time Points': 'time_points', 'Score': 'run_score'}.get(e['item'], e['item'])
                        exc[fld] = {'printed': e.get('printed'), 'calc': e.get('calc'), 'basis': e.get('basis')}
                rec['exceptions'] = exc
            findings = []
            round_id = f"{cls['event_id']}-{g}-{code}"
            if cls['tier'] != 'detail':
                # 得点のみの段階: 行の読み取り（A）だけを使い、印字の合計をそのまま持つ（第1・2層は対象外）
                ctxs.append({'cls': cls, 'meta': rmeta, 'records': records, 'findings': findings, 'ab_compared': False,
                             'rules': rules or {}})
                continue
            if rules is None:
                findings.append(Finding('error', round_id, 'layer2', f"ターン {nturn} 人の規則が {ev['rules']} に無い"))
            if b_round is None:
                findings.append(Finding('error', round_id, 'layer1', f"B 側に同じ性別・人数（{s['gender']} {len(s['athletes'])} 名）のラウンドが一つに決まらない"))
                ab = False
            else:
                findings += layer1_tokens(round_id, s, b_round['blocks'])
                ab = True
                if pace is not None and b_round.get('pace') is not None and Decimal(str(b_round['pace'])) != pace and sheet not in overrides:
                    findings.append(Finding('error', round_id, 'layer1', f"ペースタイム A/B 不一致 {pace} / {b_round['pace']}"))
            if pace_note:
                findings.append(Finding('warning', round_id, 'layer2', f"ペースタイムは規則ファイルの {pace} を使用。根拠: {pace_note}"))
            if any(r.get('_no_deductions') for r in records):
                findings.append(Finding('warning', round_id, 'layer2', '減点の印字が無い行がある（減点 0 として扱う）'))
            ctxs.append({'cls': cls, 'meta': rmeta, 'records': records, 'findings': findings, 'ab_compared': ab,
                         'rules': rules or {}})
        for p in problems + problems_b:
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': p})
        log(f"  {ev['event_id']}: {len(sections)} セクション / B {len(rounds_b)} ラウンド")
    return ctxs
