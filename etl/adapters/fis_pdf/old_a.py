"""FIS 標準の旧版（2014-15・2015-16。集計ソフト Mainstream / URTUR）のリザルトを行の並びで読む（方式 A）。

今の FIS 標準（parser_a / parser_b）と見出し・審判団・フッタは同じで、選手の欄が違う（B: / D: の印が無い、1 人 2 行）:
  2014-15（ターン点は審判 1 人 1 つ）
    1 行目: 順位 Bib FISコード 名前 国 生年 J1〜J5 ターン合計 J6 J7 ジャンプ DD エア合計 秒 タイム点 得点 [Q]
    2 行目: 2 本目の J6 J7 ジャンプ DD
  2015-16（ベース点と減点。減点は正の数で印字、ベース合計・減点合計の印字は無い）
    1 行目: 順位 Bib FISコード 名前 国 生年 ベース J1〜J5 J6 J7 ジャンプ DD エア合計 秒 タイム点 得点 [Q]
    2 行目: 減点 J1〜J5 ターン合計 2 本目の J6 J7 ジャンプ DD
ジャンプ記号は「3」のように数字だけのことがあるので、DD（小数 3 桁）の位置から前後を決める。
方式 B（old_b.py）は表頭の語の位置に数字を当てはめて読む。
"""
import re
import pdfplumber

PARSER_VERSION = 'OA-1.0'

# 名前と国名がくっつくことがある（「CHAPMAN-DAVIES RohanAUS」）
RE_L1 = re.compile(r'^(?:(\d+)\s+)?(\d+)\s+(\d{7})\s+(.+?)\s*([A-Z]{3})\s+(\d{4})\s+(.*)$')
RE_LABEL = re.compile(r'^(?:F\d|Q\d?|SF|PH):')  # 総合の報告の走りの印（「F2:7.0」のように数字とくっつく）
RE_DD = re.compile(r'^-?\d\.\d{3}$')
RE_F = re.compile(r'^-?\d+\.\d+$')
STATUS = {'DNF': 'DNF', 'DNS': 'DNS', 'DSQ': 'DSQ', 'DQ': 'DSQ'}
RE_STOP = re.compile(r'^(\d{1,2} [A-Z]{3} \d{4} /|Head Judge|NOTE|LEGEND|www\.)')


def num(s):
    v = float(s)
    return 0.0 if v == 0 else v


def unglue(toks):
    """くっついた審判点（「2.42.6」= 2.4 と 2.6）を分ける"""
    out = []
    for x in toks:
        if re.fullmatch(r'(?:\d{1,2}\.\d){2,}', x):
            out += re.findall(r'\d{1,2}\.\d', x)
        else:
            out.append(x)
    return out


def split_at_dd(toks):
    """ジャンプ記号・DD の位置で分ける → (前の数, J6, J7, 記号, DD, 後ろの語) / DD が無ければ None。
    ジャンプが無い走りは記号が無く DD が 0（「0.0 0.0 0.000」）→ 記号は None"""
    i = next((k for k, t in enumerate(toks) if RE_DD.match(t) and k >= 2), None)
    if i is None:
        return None
    if float(toks[i]) == 0 and RE_F.match(toks[i - 1]):
        return toks[:i - 2], toks[i - 2], toks[i - 1], None, toks[i], toks[i + 1:]
    if i < 3:
        return None
    return toks[:i - 3], toks[i - 3], toks[i - 2], toks[i - 1], toks[i], toks[i + 1:]


def n_turn_judges(text):
    """表頭「Code Code J1 J2 J3 J4 J5 Total J6 …」のターン審判の人数（ユニバーシアード 2015 は 3 人）"""
    m = re.search(r'Code\s+Code\s+((?:J\d\s+)+)Total', text)
    return len(m.group(1).split()) if m else 5


def parse_meta(text):
    meta = {'raw_header': text.split('\n')[:8]}
    m = re.search(r'\b[A-Z]{3}\s?(\d{1,2})\s([A-Z]{3})\s?(\d{4})\b', text)
    meta['date'] = f"{int(m.group(1))} {m.group(2)} {m.group(3)}" if m else None
    m = re.search(r"Men's Moguls|Ladies' Moguls|Women's Moguls", text)
    meta['event'] = m.group(0) if m else None
    m = re.search(r"(?:Men's|Ladies'|Women's) Moguls\s*(.*)$", text, re.M)
    meta['round'] = m.group(1).strip() if m and m.group(1).strip() else None
    m = re.search(r'/\s*(\d{4,5})\s+Report created', text)
    meta['codex'] = m.group(1) if m else None
    judges = []
    # 行の中だけで読む（審判の欄が空の行「Judge 4 (Turns):」がある。世界ジュニア 2015 は 3 人）
    for m in re.finditer(r'^Judge (\d) \((Turns|Air)\):[ \t]*(\S.*?)[ \t]+([A-Z]{3})[ \t]*$', text, re.M):
        judges.append({'judge_no': int(m.group(1)), 'role': m.group(2), 'name': m.group(3).strip(), 'noc': m.group(4)})
    meta['judges'] = judges
    officials = []
    for lab in ('FIS Technical Delegate', 'Head Judge', 'Chief of Competition', 'FIS Race Director', 'Chief of Course'):
        m = re.search(rf'^{lab}:[ \t]*(\S.+?)[ \t]+([A-Z]{{3}})?[ \t]*(?:Length:|Course Width:|Gate Width:|FIS Homologation|$)', text, re.M)
        if m:
            officials.append({'role': lab, 'name': m.group(1).strip(), 'noc': m.group(2) or ''})
    meta['officials'] = officials
    m = re.search(r'Number of Competitors:\s*(\d+)', text)
    meta['num_competitors'] = int(m.group(1)) if m else None
    m = re.search(r'Pace Time:\s*(\d+\.\d+)', text)
    meta['pace_time'] = float(m.group(1)) if m else None
    m = re.search(r'Length:\s*(\d+(?:\.\d+)?)\s*m', text)
    meta['course_length_m'] = float(m.group(1)) if m else None
    m = re.search(r'Course Width:\s*(\d+(?:\.\d+)?)\s*m', text)
    meta['course_width_m'] = float(m.group(1)) if m else None
    m = re.search(r'Gate Width:\s*(\d+(?:\.\d+)?)\s*m', text)
    meta['gate_width_m'] = float(m.group(1)) if m else None
    m = re.search(r'Gradient:\s*(\d+(?:\.\d+)?)\s*°', text)
    meta['gradient_deg'] = float(m.group(1)) if m else None
    return meta


def table_lines(text):
    """表の行（表頭の「Points」の次から、フッタ・審判長の署名欄の前まで）"""
    out, on = [], False
    for line in text.split('\n'):
        s = line.strip()
        if s == 'Points':
            on = True
            continue
        if on and RE_STOP.match(s):
            break
        if on and s:
            out.append(s)
    return out


def new_record(m, page, nj):
    rank, bib, code, name, noc, yb, rest = m.groups()
    rec = {'rank': int(rank) if rank else None, 'bib': int(bib), 'fis_code': code, 'name': name.strip(), 'noc': noc,
           'yb': int(yb), 'status': 'OK', 'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [],
           'air_total': None, 'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None, 'turns_total': None,
           'run_score': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True, 'section': None,
           'block_index': 0, 'run_label': None, 'page': page}
    toks = [RE_LABEL.sub('', x) for x in rest.split()]
    toks = unglue([x for x in toks if x])
    st = next((STATUS[t] for t in toks if t in STATUS), None)
    if st:
        rec['status'], rec['rank'] = st, None
        return rec, None
    parts = split_at_dd(toks)
    if parts is None:
        return rec, 'bad'
    pre, j6, j7, jp, dd, post = parts
    judges = [num(x) for x in pre]
    if len(judges) == nj + 1:  # 2014-15: 審判ごとのターン点と合計
        rec['base_scores'], rec['turns_total'] = judges[:nj], judges[nj]
        rec['_variant'] = 'single'
    elif len(judges) == nj:  # 2015-16: ベース点（減点と合計は 2 行目）
        rec['base_scores'] = judges
        rec['_variant'] = 'bd'
    else:
        return rec, 'bad'
    if jp:
        rec['air_jumps'].append({'J6': num(j6), 'J7': num(j7), 'jump': jp, 'DD': num(dd)})
    nums = [t for t in post if RE_F.match(t)]
    rec['air_total'], rec['seconds'], rec['time_points'], rec['run_score'] = (num(x) for x in nums[:4])
    tail = [t for t in post[4:] if t != 'Q']
    rec['tie'] = tail[0] if tail else None
    return rec, 'L2'


def parse_moguls_results(path):
    """Returns (meta, records); 1 選手 1 記録"""
    records, cur, expect, problems = [], None, None, []
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text() or '' for p in pdf.pages]
    nj = n_turn_judges(texts[0])
    for pno, text in enumerate(texts, start=1):
        for line in table_lines(text):
            m = RE_L1.match(line)
            if m and (m.group(1) or any(t in STATUS for t in m.group(7).split())):
                cur, expect = new_record(m, pno, nj)
                records.append(cur)
                if expect == 'bad':
                    problems.append((pno, line))
                continue
            if re.fullmatch(r'[A-Z][A-Z0-9 ]+', line):
                continue  # 区切り「QUALIFIED TO FINAL 2」「NOT QUALIFIED」
            mz = re.fullmatch(r"((?:[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\.]*\s+)*)(?:0\.0\s*)+", line)
            if cur is not None and cur['status'] != 'OK' and expect is None and mz:
                # 途中棄権などの行の下の 0 だけの減点の行（2015-16）。頭に折り返した名前が付くことがある
                if mz.group(1).strip():
                    cur['name'] += ' ' + mz.group(1).strip()
                continue
            toks = unglue([x for x in (RE_LABEL.sub('', y) for y in line.split()) if x])
            if expect == 'L2' and cur is not None:
                # 2 行目の頭に折り返した名前（「Laurianne 4.3 5.2 bT 0.800」）
                k = 0
                while k < len(toks) and re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\.]*", toks[k]) and k + 1 < len(toks) and not RE_DD.match(toks[k + 1]):
                    k += 1
                if k:
                    cur['name'] += ' ' + ' '.join(toks[:k])
                    toks = toks[k:]
                parts = split_at_dd(toks)
                if cur.get('_variant') == 'single' and len(toks) == 3 and RE_DD.match(toks[2]) and num(toks[2]) == 0:
                    expect = None  # 2 本目のジャンプが無い（「0.0 0.0 0.000」）
                    continue
                if cur.get('_variant') == 'single' and parts and not parts[0]:
                    _, j6, j7, jp, dd, _ = parts
                    if jp:
                        cur['air_jumps'].append({'J6': num(j6), 'J7': num(j7), 'jump': jp, 'DD': num(dd)})
                    expect = None
                    continue
                if cur.get('_variant') == 'bd' and parts and len(parts[0]) == nj + 1:
                    pre, j6, j7, jp, dd, _ = parts
                    cur['ded_scores'] = [num('-' + x) if not x.startswith('-') else num(x) for x in pre[:nj]]
                    cur['turns_total'] = num(pre[nj])
                    if jp:
                        cur['air_jumps'].append({'J6': num(j6), 'J7': num(j7), 'jump': jp, 'DD': num(dd)})
                    expect = None
                    continue
            problems.append((pno, line))
    for r in records:
        r.pop('_variant', None)
    meta = parse_meta(texts[0] + '\n' + texts[-1])
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records


if __name__ == '__main__':  # pragma: no cover
    import json, sys
    m, r = parse_moguls_results(sys.argv[1])
    print(json.dumps({k: v for k, v in m.items() if k != 'raw_header'}, ensure_ascii=False, indent=1))
    print(len(r), 'records')
    for x in r[:3]:
        print(json.dumps(x, ensure_ascii=False))
