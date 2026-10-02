"""ターンの列が先、エアの列が後に並ぶ FIS リザルト（2017 札幌アジア大会。報告書 FRM020901）を行の語の並びで読む（方式 A）。

1 選手 3 行:
  順位 Bib FISコード 名前 国 生年 秒 タイム点 B: J1..Jn ベース合計 J4 J5 技 DD 得点 [Q] [同点]
  D: 減点1..n 減点合計 J4 J5 技 DD                      （2 本目のジャンプ）
  タイム点 ターン合計 エア合計
W杯の様式（エアの列が先、ターン合計・エア合計が B/D の行の中）とは列の並びが違うので parser_a の座標の帯では読めない。
DD（小数 3 桁）を錨にして、その前を「ベース合計 J4 J5 技」、さらに前をターン審判の点として読む（技コードが数字 '3' の年がある）。
減点がくっついて印字される（'-11.6-12.4'）ので分ける。失格などは B: の後に 'DSQ' だけ印字される。
方式 B（tf_b.py）は語の座標を表頭の見出し語の位置に当てはめて読む。
"""
import re
import pdfplumber

PARSER_VERSION = 'TA-1.0'

RE_ROW = re.compile(r'^(?:(\d+)\s+)?(\d+)\s+(\d{7})\s+(.+?)\s*([A-Z]{3})\s+(\d{4})\s+(.*)$')
DD = re.compile(r'^\d\.\d{3}$')
NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
STATUS = {'DNF': 'DNF', 'DNS': 'DNS', 'DSQ': 'DSQ', 'DQ': 'DSQ'}


def num(s):
    v = float(s)
    return 0.0 if v == 0 else v


def split_glued(toks):
    """'-11.6-12.4' → ['-11.6', '-12.4']（減点の欄の数がくっついた語）"""
    out = []
    for t in toks:
        parts = re.findall(r'-?\d+\.\d+', t)
        out += parts if len(parts) >= 2 and ''.join(parts) == t else [t]
    return out


def parse_meta(text):
    meta = {'raw_header': text.split('\n')[:8]}
    m = re.search(r'\b[A-Z]{3}\s+(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\b', text)
    meta['date'] = f"{int(m.group(1))} {m.group(2)} {m.group(3)}" if m else None
    m = re.search(r"^(MEN'S|LADIES'|WOMEN'S)\s+MOGULS$", text, re.M)
    meta['event'] = {"MEN'S": "Men's Moguls", "LADIES'": "Ladies' Moguls", "WOMEN'S": "Women's Moguls"}[m.group(1)] if m else None
    m = re.search(r"MOGULS\n(QUALIFICATION|FINAL \d|FINAL|SUPER FINAL)\n", text)
    meta['round'] = m.group(1).title() if m else None
    meta['codex'] = None  # この様式には CODEX の印字が無い（registry の codex を使う）
    meta['judges'] = [{'judge_no': int(m.group(1)), 'role': m.group(2), 'name': m.group(3).strip(), 'noc': m.group(4)}
                      for m in re.finditer(r'Judge (\d) \((Turns|Air)\): (.+?) ([A-Z]{3})\b', text)]
    officials = []
    for lab in ('FIS Technical Delegate', 'Head Judge', 'Chief of Competition', 'Chief of Course'):
        m = re.search(re.escape(lab) + r': (.+?) ([A-Z]{3})\b', text)
        if m:
            officials.append({'role': lab, 'name': m.group(1).strip(), 'noc': m.group(2)})
    meta['officials'] = officials
    m = re.search(r'Number of Competitors:\s*(\d+)', text)
    meta['num_competitors'] = int(m.group(1)) if m else None
    m = re.search(r'Pace Time:\s*(\d+\.\d+)s', text)
    meta['pace_time'] = float(m.group(1)) if m else None
    m = re.search(r'Length:\s*(\d+(?:\.\d+)?)m', text)
    meta['course_length_m'] = float(m.group(1)) if m else None
    m = re.search(r'Course Width:\s*(\d+(?:\.\d+)?)m', text)
    meta['course_width_m'] = float(m.group(1)) if m else None
    m = re.search(r'Gate Width:\s*(\d+(?:\.\d+)?)m', text)
    meta['gate_width_m'] = float(m.group(1)) if m else None
    m = re.search(r'Gradient:\s*(\d+(?:\.\d+)?)°', text)
    meta['gradient_deg'] = float(m.group(1)) if m else None
    return meta


def _jump_side(toks):
    """DD を錨に (審判の点, 合計, J4, J5, 技, DD, DD より後の語) を返す。DD が無ければ None"""
    d = next((i for i, t in enumerate(toks) if DD.match(t)), None)
    if d is None or d < 4:
        return None
    return toks[:d - 4], toks[d - 4], toks[d - 3], toks[d - 2], toks[d - 1], toks[d], toks[d + 1:]


def parse_moguls_results(path, pages=None):
    """Returns (meta, records); 1 選手 1 記録。pages: そのラウンドの報告書のページ（PDF 全体での番号）"""
    records, problems = [], []
    with pdfplumber.open(path, pages=pages) as pdf:
        texts = [(p.page_number, p.extract_text() or '') for p in pdf.pages]
    for pno, text in texts:
        on = False
        cur, expect = None, None
        for line in text.split('\n'):
            s = line.strip()
            if s == 'Points':
                on = True
                continue
            if not on or not s:
                continue
            if s.startswith(('FRM', 'Head Judge')) or re.search(r'\bPage \d', s):  # 表の終わり（署名欄・ページの下端）
                on = False
                continue
            m = RE_ROW.match(s)
            if m:
                rank, bib, code, name, noc, yb, rest = m.groups()
                cur = {'rank': int(rank) if rank else None, 'bib': int(bib), 'fis_code': code, 'name': name.strip(), 'noc': noc,
                       'yb': int(yb), 'status': 'OK', 'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [],
                       'air_total': None, 'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None,
                       'turns_total': None, 'run_score': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True,
                       'section': None, 'block_index': 0, 'run_label': None, 'page': pno}
                records.append(cur)
                head, _, tail = rest.partition('B:')
                toks = tail.split()
                if len(toks) == 1 and toks[0] in STATUS:
                    cur['status'], cur['rank'] = STATUS[toks[0]], None
                    expect = 'D-empty'
                    continue
                hv = head.split()
                js = _jump_side(toks)
                if len(hv) != 2 or js is None:
                    problems.append((pno, s))
                    cur, expect = None, None
                    continue
                cur['seconds'], cur['time_points'] = num(hv[0]), num(hv[1])
                judges, tot, a, b, jump, dd, after = js
                cur['base_scores'] = [num(x) for x in judges]
                cur['base_total'] = num(tot)
                cur['air_jumps'].append({'J6': num(a), 'J7': num(b), 'jump': jump, 'DD': num(dd)})
                after = [t for t in after if t != 'Q']  # 'Q' は予選通過の印
                if not after or not NUM.match(after[0]) or len(after) > 2:
                    problems.append((pno, s))
                    continue
                cur['run_score'] = num(after[0])
                if len(after) == 2:
                    cur['tie'] = num(after[1])
                expect = 'D'
                continue
            if cur is None:
                problems.append((pno, s))
                continue
            if expect == 'D-empty' and s == 'D:':
                expect = None
                continue
            if expect == 'D' and s.startswith('D:'):
                js = _jump_side(split_glued(s[2:].split()))
                if js is None or js[6]:
                    problems.append((pno, s))
                    continue
                judges, tot, a, b, jump, dd, _ = js
                cur['ded_scores'] = [num(x) for x in judges]
                cur['ded_total'] = num(tot)
                cur['air_jumps'].append({'J6': num(a), 'J7': num(b), 'jump': jump, 'DD': num(dd)})
                expect = 'T'
                continue
            if expect == 'T':
                t = s.split()
                if len(t) == 3 and all(NUM.match(x) for x in t) and num(t[0]) == cur['time_points']:
                    cur['turns_total'], cur['air_total'] = num(t[1]), num(t[2])
                    expect = None
                    continue
            problems.append((pno, s))
    meta = parse_meta(texts[0][1])
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
