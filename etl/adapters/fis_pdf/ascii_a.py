"""文字表（「=== ===」の下線がある等幅の表。カナダの集計ソフト Winfree）の FIS リザルトを行の並びで読む（方式 A）。

NAC 2022 Apex・Val St-Côme の様式（英語・仏語）。1 人 = 3〜4 行:
  1 行目: 順位 Bib 名前 FIS# 組 国 ベース点 J1〜J5 ベース合計       （途中棄権などは dnf / dns が並ぶ）
  2 行目: 減点 J1〜J5 減点合計                                      （「-.0」は 0、合計と J5 がくっつくことがある）
  3 行目: ターン合計 J6 J7 ジャンプ DD エア合計 審判点 秒 タイム点 得点（タイム点 0 は空欄）
  4 行目: 2 本目の J6 J7 ジャンプ DD                                （ジャンプが無ければ「.0 .0」だけ）
2 本目のジャンプの行が次のページの頭に送られることがあるので、ページをまたいで行を続けて読む。
方式 B（ascii_b.py）は文字の位置を下線の列に当てはめて読む。
"""
import re
import pdfplumber

PARSER_VERSION = 'AA-1.0'

MONTHS_EN = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
MONTHS_FR = {'janv': 1, 'févr': 2, 'fév': 2, 'mars': 3, 'avr': 4, 'mai': 5, 'juin': 6, 'juil': 7, 'août': 8, 'sept': 9,
             'oct': 10, 'nov': 11, 'déc': 12}
MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']

NUM = r'-?\d*\.\d+'
RE_L1 = re.compile(r'^(\d+)\s+(\d+)\s+(.+?)\s+(\d{7})\s+(\S+)\s+([A-Z]{3})\s+(.*)$')
RE_L3 = re.compile(rf'^({NUM})\s+({NUM})\s+({NUM})\s+(?:(\S+)\s+(-?\d\.\d{{3}})\s+)?(\d+\.\d\d)\s+(\d+\.\d\d)\s+(\d+\.\d\d)\s+'
                   rf'(?:(\d+\.\d\d)\s+)?(\d+\.\d\d)$')
RE_L4 = re.compile(rf'^({NUM})\s+({NUM})(?:\s+(\S+)\s+(-?\d\.\d{{3}}))?$')
RE_NUMS_ONLY = re.compile(rf'^(?:{NUM}\s*)+$')
RE_FOOT = re.compile(r'^(Male|Mâle|Winfree|Filename|Nom de fichier)\b')
STATUS = {'dnf': 'DNF', 'dns': 'DNS', 'dsq': 'DSQ', 'dq': 'DSQ'}


def num(s):
    v = float(s)
    return 0.0 if v == 0 else v


def date_of(line):
    """'Jan 29, 2022 (Sat)' / '5 Févr 2022 (Sam)' -> '29 JAN 2022'"""
    m = re.search(r'\b([A-Za-z]{3})[a-z]*\.? (\d{1,2}), (\d{4})', line)
    if m and m.group(1).lower() in MONTHS_EN:
        return f"{int(m.group(2))} {MON[MONTHS_EN[m.group(1).lower()] - 1]} {m.group(3)}"
    m = re.search(r'\b(\d{1,2}) ([A-Za-zéû]+)\.? (\d{4})', line)
    if m and m.group(2).lower() in MONTHS_FR:
        return f"{int(m.group(1))} {MON[MONTHS_FR[m.group(2).lower()] - 1]} {m.group(3)}"
    return None


def person(s):
    """'HUTHCINGS,Ian' -> 'HUTHCINGS Ian'（印字のまま、区切りのコンマだけ空白に）"""
    return re.sub(r'\s*,\s*', ' ', s.strip())


def parse_meta(text):
    lines = text.split('\n')
    meta = {'raw_header': lines[:8]}
    meta['date'] = date_of(lines[2]) if len(lines) > 2 else None
    m = re.search(r'\((\d{4,5})\)\s*$', lines[2]) if len(lines) > 2 else None
    meta['codex'] = m.group(1) if m else None
    gm = re.search(r'^(Female|Male|Femme|Homme)\b', text, re.M)
    fem = bool(gm) and gm.group(1) in ('Female', 'Femme')
    meta['event'] = ("Women's Moguls" if fem else "Men's Moguls") if gm else None
    m = re.search(r'^(.*?)\s+(?:Run|Descente|Event)\s+Date:', text, re.M)
    meta['round'] = m.group(1).strip() if m else None
    judges = []
    for m in re.finditer(r'(?:Judge|Juge) (\d)\((T&L|Tech|Air|Saut)\):\s*(\S+)', text):
        judges.append({'judge_no': int(m.group(1)), 'role': 'Turns' if m.group(2) in ('T&L', 'Tech') else 'Air',
                       'name': person(m.group(3)), 'noc': ''})
    meta['judges'] = judges
    officials = []
    for lab, pat in (('Head Judge', r'(?:Head Judge|Juge en Chef):\s*(\S+)\s+(?:Course|Parcours):'),
                     ('Chief of Competition', r'(?:Chief of Comp|Chef de Comp):\s*(\S+)\s+(?:Length|Longueur):'),
                     ('Technical Delegate', r'(?:T\.D\.|D\.T\.):\s*(\S+)\s+(?:Width|Largeur):'),
                     ('Chief of Scoring', r'(?:Chief of Scoring|Chef de Compilation):\s*(\S+)\s+(?:Pitch|Inclinaison):')):
        m = re.search(pat, text)
        if m:
            officials.append({'role': lab, 'name': person(m.group(1)), 'noc': ''})
    meta['officials'] = officials
    m = re.search(r'(?:Pace|Temps de base):\s*(?:Male|Homme)\s*=\s*(\d+\.\d+),\s*(?:Female|Femme)\s*=\s*(\d+\.\d+)', text)
    meta['pace_time'] = (float(m.group(2)) if fem else float(m.group(1))) if m and gm else None
    m = re.search(r'(?:Length|Longueur):\s*(\d+(?:\.\d+)?)\s*m', text)
    meta['course_length_m'] = float(m.group(1)) if m else None
    m = re.search(r'(?:Width|Largeur):\s*(\d+(?:\.\d+)?)\s*m', text)
    meta['course_width_m'] = float(m.group(1)) if m else None
    meta['gate_width_m'] = None
    m = re.search(r'(?:Pitch|Inclinaison):\s*(\d+(?:\.\d+)?)\s*deg', text)
    meta['gradient_deg'] = float(m.group(1)) if m else None
    m = re.search(r'Cutoff:\s*(\d+)', text)
    meta['cutoff'] = int(m.group(1)) if m else None
    meta['num_competitors'] = None  # この様式には出場人数の印字が無い
    return meta


def table_lines(page_text):
    """ページの表の行（下線「=== ===」の次からフッタの前まで）"""
    out, on = [], False
    for line in page_text.split('\n'):
        s = line.strip()
        if s.startswith('=== ==='):
            on = True
            continue
        if not on:
            continue
        if RE_FOOT.match(s):
            break
        if s:
            out.append(s)
    return out


def new_record(m, page):
    rank, bib, name, fis, _grp, noc, rest = m.groups()
    rec = {'rank': int(rank), 'bib': int(bib), 'fis_code': fis, 'name': person(name), 'noc': noc, 'yb': None,
           'status': 'OK', 'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None,
           'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None, 'turns_total': None, 'run_score': None,
           'tie': None, 'q_block': None, 'best_score': None, 'counting': True, 'section': None, 'block_index': 0,
           'run_label': None, 'page': page}
    toks = rest.split()
    st = next((STATUS[t.lower()] for t in toks if t.lower() in STATUS), None)
    if st:
        rec['status'], rec['rank'] = st, None
        return rec, None
    vals = re.findall(NUM, rest)
    rec['base_scores'] = [num(v) for v in vals[:-1]]
    rec['base_total'] = num(vals[-1])
    return rec, 'L2'


def parse_moguls_results(path):
    """Returns (meta, records); 1 選手 1 記録"""
    records, cur, expect, problems = [], None, None, []
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ''
        for pno, page in enumerate(pdf.pages, start=1):
            for line in table_lines(page.extract_text() or ''):
                m = RE_L1.match(line)
                if m:
                    cur, expect = new_record(m, pno)
                    records.append(cur)
                    continue
                if cur is None:
                    problems.append((pno, line))
                    continue
                if expect == 'L2' and RE_NUMS_ONLY.match(line):
                    vals = re.findall(NUM, line)
                    cur['ded_scores'] = [num(v) for v in vals[:-1]]
                    cur['ded_total'] = num(vals[-1])
                    expect = 'L3'
                    continue
                m = RE_L3.match(line)
                if expect == 'L3' and m:
                    tt, j6, j7, jp, dd, airs, _judge, sec, pts, run = m.groups()
                    cur['turns_total'] = num(tt)
                    if jp:
                        cur['air_jumps'].append({'J6': num(j6), 'J7': num(j7), 'jump': jp, 'DD': num(dd)})
                    cur['air_total'], cur['seconds'], cur['run_score'] = num(airs), num(sec), num(run)
                    cur['time_points'] = num(pts) if pts else 0.0  # タイム点 0 は空欄で印字される
                    expect = 'L4'
                    continue
                m = RE_L4.match(line)
                if expect == 'L4' and m:
                    if m.group(3):
                        cur['air_jumps'].append({'J6': num(m.group(1)), 'J7': num(m.group(2)), 'jump': m.group(3), 'DD': num(m.group(4))})
                    expect = None
                    continue
                problems.append((pno, line))
    meta = parse_meta(first)
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
