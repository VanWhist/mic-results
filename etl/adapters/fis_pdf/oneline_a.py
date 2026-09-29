"""1 人 1 行の FIS リザルト（世界ジュニア 2016 Åre。主催者の集計ソフト）を行の語の並びで読む（方式 A）。

  順位 Bib FISコード 名前 国 J1 J2 J3 ターン合計 J4 J5 記号 DD J4 J5 記号 DD エア合計 秒 タイム点 得点
ターン審判 3 人（1 人 1 つの点、合計は 3 人の和）、エア審判 2 人（J4・J5）で 2 本のジャンプが同じ行に並ぶ。
文字の間の空白が狭く、既定の読み方では語がくっつく（「8.17opA」）ので、文字の間隔を細かく取って（x_tolerance=1）読む。
途中棄権などは順位 0 と行末の「DNF」。小数点がコンマの数（ペースタイム「23,78」）がある。
方式 B（oneline_b.py）は 1 文字ずつの座標を表頭の見出し語の位置に当てはめて読む。
"""
import re
import pdfplumber

PARSER_VERSION = 'LA-1.0'

RE_ROW = re.compile(r'^(\d+)\s+(\d+)\s+(\d{7})\s+(.+?)\s+([A-Z]{3})\s+(-?\d.*)$')
STATUS = {'DNF': 'DNF', 'DNS': 'DNS', 'DSQ': 'DSQ', 'DQ': 'DSQ'}
MON = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')


def num(s):
    v = float(s.replace(',', '.'))
    return 0.0 if v == 0 else v


def parse_meta(text):
    meta = {'raw_header': text.split('\n')[:8]}
    m = re.search(r'\b[A-Z]{3}\s*(\d{1,2})\s*(' + '|'.join(MON) + r')\s*(\d{4})\b', text)
    meta['date'] = f"{int(m.group(1))} {m.group(2)} {m.group(3)}" if m else None
    m = re.search(r"(Men[´'’]s|Ladies[´'’]|Women[´'’]s)\s*Moguls\s*(.*)$", text, re.M)
    meta['event'] = {'M': "Men's Moguls", 'L': "Ladies' Moguls", 'W': "Women's Moguls"}[m.group(1)[0]] if m else None
    meta['round'] = m.group(2).strip() or None if m else None
    m = re.search(r'/\s*[A-Z]{3}(\d{4,5})\s*Page', text)
    meta['codex'] = m.group(1) if m else None
    judges = []
    for m in re.finditer(r'Judge\s*(\d)\s*:\s*\((Turns|Air)\)\s*(.+?)\s*\(([A-Z]{3})\)', text):
        judges.append({'judge_no': int(m.group(1)), 'role': m.group(2), 'name': m.group(3).strip(), 'noc': m.group(4)})
    meta['judges'] = judges
    officials = []
    for lab, pat in (('FIS Technical Delegate', r'FIS\s*Technical\s*Delegate\s*:'), ('Head Judge', r'Head\s*Judge\s*:'),
                     ('Chief of Competition', r'Chief\s*of\s*competition\s*:'), ('FIS Race Director', r'FIS\s*Race\s*Director\s*:'),
                     ('Chief of Course', r'Chief\s*of\s*Course\s*:')):
        m = re.search(pat + r'\s*(.+?)\s*\(([A-Z]{3})\)', text)
        if m:
            officials.append({'role': lab, 'name': m.group(1).strip(), 'noc': m.group(2)})
    meta['officials'] = officials
    meta['num_competitors'] = None  # 出場人数の印字が無い
    m = re.search(r'Pace\s*Time:\s*(\d+[,.]\d+)', text)
    meta['pace_time'] = num(m.group(1)) if m else None
    m = re.search(r'Length:\s*(\d+(?:[,.]\d+)?)\s*m', text)
    meta['course_length_m'] = num(m.group(1)) if m else None
    m = re.search(r'Course\s*Width:\s*(\d+(?:[,.]\d+)?)\s*m', text)
    meta['course_width_m'] = num(m.group(1)) if m else None
    meta['gate_width_m'] = None
    m = re.search(r'Gradient:\s*(\d+(?:[,.]\d+)?)\s*degrees', text)
    meta['gradient_deg'] = num(m.group(1)) if m else None
    return meta


def parse_moguls_results(path):
    """Returns (meta, records); 1 選手 1 記録"""
    records, problems = [], []
    with pdfplumber.open(path) as pdf:
        texts = [p.extract_text(x_tolerance=1) or '' for p in pdf.pages]
    for pno, text in enumerate(texts, start=1):
        on = False
        for line in text.split('\n'):
            s = line.strip()
            if s == 'Points':
                on = True
                continue
            if not on or not s:
                continue
            if re.search(r'\bPage\s*\d', s):
                break
            m = RE_ROW.match(s)
            if not m:
                problems.append((pno, s))
                continue
            rank, bib, code, name, noc, rest = m.groups()
            toks = rest.split()
            rec = {'rank': int(rank) or None, 'bib': int(bib), 'fis_code': code, 'name': name.strip(), 'noc': noc, 'yb': None,
                   'status': 'OK', 'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [],
                   'air_total': None, 'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None,
                   'turns_total': None, 'run_score': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True,
                   'section': None, 'block_index': 0, 'run_label': None, 'page': pno}
            records.append(rec)
            if toks[-1] in STATUS:
                rec['status'], rec['rank'] = STATUS[toks[-1]], None
                continue
            if len(toks) != 16:
                problems.append((pno, s))
                continue
            rec['base_scores'] = [num(x) for x in toks[0:3]]
            rec['turns_total'] = num(toks[3])
            for k in (4, 8):
                rec['air_jumps'].append({'J6': num(toks[k]), 'J7': num(toks[k + 1]), 'jump': toks[k + 2], 'DD': num(toks[k + 3])})
            rec['air_total'], rec['seconds'], rec['time_points'], rec['run_score'] = (num(x) for x in toks[12:16])
    meta = parse_meta(texts[0])
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
