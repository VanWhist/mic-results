"""1 人 1 行の FIS リザルト（世界ジュニア 2016 Åre）を、1 文字ずつの座標で読む（方式 B）。

方式 A（oneline_a.py）が行の語の並びで読むのに対し、こちらは文字を間隔で語にまとめ（空白の印字が無いので、
文字の間が 0.8pt 以上あけば別の語）、表頭の見出し語（J1 J2 J3 Total J4 J5 Jump DD J4 J5 Jump DD Total Time Time Score）の
横の重なりが一番大きい列（重ならなければ隙間の一番狭い列）に当てはめる。名前は FIS コードの右から国名の列の左まで。
"""
import re
import pdfplumber

PARSER_VERSION = 'LB-1.0'

KEYS = ['t1', 't2', 't3', 'ttot', 'j1a', 'j1b', 'j1jump', 'j1dd', 'j2a', 'j2b', 'j2jump', 'j2dd', 'atot', 'sec', 'pts', 'score']
MON = {'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'}


def _f(s):
    v = float(s.replace(',', '.'))
    return 0.0 if v == 0 else v


def _words(chars, gap=0.8):
    rows = {}
    for c in chars:
        if not c['text'].strip():
            continue
        key = next((k for k in rows if abs(k - c['top']) < 1.5), c['top'])
        rows.setdefault(key, []).append(c)
    out = []
    for top, cs in sorted(rows.items()):
        cs.sort(key=lambda c: c['x0'])
        ws, cur = [], None
        for c in cs:
            if cur and c['x0'] - cur['x1'] < gap:
                cur['text'] += c['text']
                cur['x1'] = c['x1']
            else:
                cur = {'text': c['text'], 'x0': c['x0'], 'x1': c['x1'], 'top': top}
                ws.append(cur)
        out.append((top, ws))
    return out


def _meta(lines):
    texts = [' '.join(w['text'] for w in ws) for _, ws in lines]
    meta = {'raw_header': texts[:8]}
    meta['date'] = None
    for l in texts[:8]:
        toks = l.split()
        for i in range(len(toks) - 2):
            if toks[i].isdigit() and toks[i + 1] in MON and re.fullmatch(r'\d{4}', toks[i + 2]):
                meta['date'] = f"{int(toks[i])} {toks[i + 1]} {toks[i + 2]}"
                break
        if meta['date']:
            break
    ev = next((l for l in texts[:6] if 'Moguls' in l), '')
    head = ev.split('Moguls')[0].strip()
    meta['event'] = ("Men's Moguls" if head.startswith('Men') else "Ladies' Moguls" if head.startswith('Ladies')
                     else "Women's Moguls" if head.startswith('Women') else None)
    meta['round'] = ev.split('Moguls', 1)[1].strip() or None if 'Moguls' in ev else None
    foot = next((l for l in texts if 'Page' in l.split()), '')
    meta['codex'] = next((t[3:] for t in foot.split() if re.fullmatch(r'[A-Z]{3}\d{4,5}', t)), None)
    judges, officials = [], []
    for l in texts:
        toks = l.split()
        if len(toks) >= 5 and toks[0] == 'Judge' and toks[1].isdigit() and toks[2] == ':' and toks[3] in ('(Turns)', '(Air)'):
            end = next(i for i in range(4, len(toks)) if re.fullmatch(r'\([A-Z]{3}\)', toks[i]))
            judges.append({'judge_no': int(toks[1]), 'role': toks[3].strip('()'), 'name': ' '.join(toks[4:end]), 'noc': toks[end].strip('()')})
        for lab, words in (('FIS Technical Delegate', ['FIS', 'Technical', 'Delegate']), ('Head Judge', ['Head', 'Judge']),
                           ('Chief of Competition', ['Chief', 'of', 'competition']), ('FIS Race Director', ['FIS', 'Race', 'Director']),
                           ('Chief of Course', ['Chief', 'of', 'Course'])):
            n = len(words)
            if toks[:n] == words or toks[:n] == words[:-1] + [words[-1] + ':']:
                rest = toks[n:]
                if rest and rest[0] == ':':
                    rest = rest[1:]
                end = next((i for i, t in enumerate(rest) if re.fullmatch(r'\([A-Z]{3}\)', t)), None)
                if end:
                    officials.append({'role': lab, 'name': ' '.join(rest[:end]), 'noc': rest[end].strip('()')})
    meta['judges'] = sorted(judges, key=lambda j: j['judge_no'])
    meta['officials'] = officials

    def value(label, unit=''):
        for l in texts:
            i = l.find(label)
            if i >= 0:
                m = re.match(r'\s*(\d+(?:[,.]\d+)?)\s*' + unit, l[i + len(label):])
                if m:
                    return _f(m.group(1))
        return None
    meta['pace_time'] = value('Pace Time:')
    meta['course_length_m'] = value('Length:', 'm')
    meta['course_width_m'] = value('Course Width:', 'm')
    meta['gate_width_m'] = None
    meta['gradient_deg'] = value('Gradient:', 'degrees')
    meta['num_competitors'] = None
    return meta


def parse_moguls_results(path):
    records, problems = [], []
    with pdfplumber.open(path) as pdf:
        pages = [_words(p.chars) for p in pdf.pages]
    meta = _meta(pages[0])
    for pno, lines in enumerate(pages, start=1):
        # 表頭は「Rank Bib FIS Code Name … Score」と「Code J1 J2 J3 Total …」が少しずれた高さに印字される
        rank_top = next((top for top, ws in lines if ws and ws[0]['text'] == 'Rank'), None)
        pts = next((w for _, ws in lines for w in ws if w['text'] == 'Points'), None)
        if rank_top is None or pts is None:
            continue
        hdr = sorted((w for top, ws in lines if rank_top - 1 <= top < pts['top'] - 1 for w in ws), key=lambda w: w['x0'])
        if not any(w['text'] == 'J1' for w in hdr):
            continue
        j1 = next(i for i, w in enumerate(hdr) if w['text'] == 'J1')
        vals = hdr[j1:j1 + 16]
        # 値の語は見出し語と横に重なる（短い記号「3」は見出し「Jump」の右端の下）。重なりが一番大きい列、無ければ隙間の一番狭い列
        spans = {k: (w['x0'], w['x1']) for k, w in zip(KEYS, vals)}
        noc_x = next(w for w in hdr[:j1] if w['text'] == 'Code' and w['x0'] > hdr[3]['x0'] + 40)
        for top, ws in lines:
            if top <= pts['top'] + 1:
                continue
            if any(w['text'].startswith('Page') for w in ws):
                break
            code = next((w for w in ws if re.fullmatch(r'\d{7}', w['text'])), None)
            if code is None:
                problems.append((pno, ' '.join(w['text'] for w in ws)))
                continue
            left = [w for w in ws if w['x1'] <= code['x0']]
            noc = next((w for w in ws if re.fullmatch(r'[A-Z]{3}', w['text']) and abs(w['x0'] - noc_x['x0']) < 12), None)
            name = [w['text'] for w in ws if code['x1'] < w['x0'] and (noc is None or w['x1'] <= noc['x0'])]
            rest = [w for w in ws if noc is not None and w['x0'] >= noc['x1']]
            rec = {'rank': int(left[0]['text']) or None, 'bib': int(left[-1]['text']), 'fis_code': code['text'],
                   'name': ' '.join(name), 'noc': noc['text'] if noc else None, 'yb': None, 'status': 'OK',
                   'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None,
                   'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None, 'turns_total': None,
                   'run_score': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True, 'section': None,
                   'block_index': 0, 'run_label': None, 'page': pno}
            records.append(rec)
            st = next((w['text'] for w in rest if w['text'] in ('DNF', 'DNS', 'DSQ', 'DQ')), None)
            if st:
                rec['status'], rec['rank'] = ('DSQ' if st == 'DQ' else st), None
                continue
            cols = {k: [] for k in KEYS}
            for w in rest:
                ov = {k: min(w['x1'], x1) - max(w['x0'], x0) for k, (x0, x1) in spans.items()}
                k = max(ov, key=ov.get)
                if ov[k] <= 0:  # 重ならなければ、見出し語との隙間が一番狭い列
                    k = min(spans, key=lambda k: max(spans[k][0] - w['x1'], w['x0'] - spans[k][1]))
                cols[k].append(w)
            # 1 つの列に 2 語入ったら、空いている隣の列へ寄せる（幅の狭い DD「0」は右の Total 寄りに印字される）
            moved = True
            while moved:
                moved = False
                for i, k in enumerate(KEYS):
                    if len(cols[k]) < 2:
                        continue
                    cols[k].sort(key=lambda w: w['x0'])
                    if i > 0 and not cols[KEYS[i - 1]]:
                        cols[KEYS[i - 1]].append(cols[k].pop(0))
                        moved = True
                    elif i + 1 < len(KEYS) and not cols[KEYS[i + 1]]:
                        cols[KEYS[i + 1]].append(cols[k].pop())
                        moved = True
            gap = lambda k, w: max(0.0, spans[k][0] - w['x1'], w['x0'] - spans[k][1])
            if any(gap(k, w) > 8 for k, ws_ in cols.items() for w in ws_):
                problems.append((pno, ' '.join(w['text'] for w in ws)))
                continue
            cell = {k: [w['text'] for w in v] for k, v in cols.items() if v}
            if any(len(v) != 1 for v in cell.values()) or set(cell) != set(KEYS):
                problems.append((pno, ' '.join(w['text'] for w in ws)))
                continue
            g = {k: v[0] for k, v in cell.items()}
            rec['base_scores'] = [_f(g['t1']), _f(g['t2']), _f(g['t3'])]
            rec['turns_total'] = _f(g['ttot'])
            for p in ('j1', 'j2'):
                rec['air_jumps'].append({'J6': _f(g[p + 'a']), 'J7': _f(g[p + 'b']), 'jump': g[p + 'jump'], 'DD': _f(g[p + 'dd'])})
            rec['air_total'], rec['seconds'] = _f(g['atot']), _f(g['sec'])
            rec['time_points'], rec['run_score'] = _f(g['pts']), _f(g['score'])
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
