"""ターンの列が先、エアの列が後に並ぶ FIS リザルト（2017 札幌アジア大会）を、語の座標で読む（方式 B）。

方式 A（tf_a.py）が行の語の並び（DD を錨にする）で読むのに対し、こちらは表頭の見出し語
（Seconds Points B D J1..Jn Total J4 J5 Jump DD Total Score Tie）の x 座標で列の範囲を決め、各語の左端がどの列に入るかで読む。
選手のまとまりは FIS コード（7 桁）の語がある行から始まる 3 行。くっついた減点（'-11.6-12.4'）は、その語の左端の列から
右へ順に並べる。名前は FIS コードの右から国の列の手前まで（国がくっついた語 'CooperAUS' は末尾 3 文字を国にする）。
"""
import re
import pdfplumber

PARSER_VERSION = 'TB-1.0'

NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
STATUS = {'DNF': 'DNF', 'DNS': 'DNS', 'DSQ': 'DSQ', 'DQ': 'DSQ'}


def _f(s):
    v = float(s)
    return 0.0 if v == 0 else v


def _rows(words):
    rows = {}
    for w in words:
        key = next((k for k in rows if abs(k - w['top']) < 2), w['top'])
        rows.setdefault(key, []).append(w)
    return [(top, sorted(ws, key=lambda w: w['x0'])) for top, ws in sorted(rows.items())]


def _columns(words):
    """表頭の 2 段目（'Code Seconds B D J1 …'）の語から列の左端の一覧 [(x0, 名前)] を作る。無ければ None"""
    sec = next((w for w in words if w['text'] == 'Seconds'), None)
    if sec is None:
        return None
    hdr = sorted([w for w in words if abs(w['top'] - sec['top']) < 2], key=lambda w: w['x0'])
    cols, n_total, seen_dd = [], 0, False
    pts = next((w for w in words if w['text'] == 'Points' and w['x0'] > sec['x0']), None)
    cols.append((sec['x0'] - 12, 'sec'))
    if pts:
        cols.append((pts['x0'] - 4, 'pts'))
    for w in hdr:
        t = w['text']
        if t == 'B':
            cols.append((w['x0'] - 2, 'label'))
        elif re.fullmatch(r'J\d', t):
            cols.append((w['x0'] - 8, ('air' if seen_dd or n_total else 'turn') + t[1]))
        elif t == 'Total':
            n_total += 1
            cols.append((w['x0'] - 4, 'turns_total' if n_total == 1 else 'air_total'))
        elif t == 'Jump':
            cols.append((w['x0'] - 2, 'jump'))
        elif t == 'DD':
            seen_dd = True
            cols.append((w['x0'] - 10, 'dd'))
    score = next((w for w in words if w['text'] == 'Score' and w['x0'] > 450 and w['top'] < sec['top']), None)
    tie = next((w for w in words if w['text'] == 'Tie' and w['top'] < sec['top']), None)
    if score:
        cols.append((score['x0'] - 4, 'score'))
    if tie:
        cols.append((tie['x0'] - 4, 'tie'))
    code = next((w for w in hdr if w['text'] == 'Code'), None)  # 国の列（表頭 'NOC' の下の 'Code'）
    return sorted(cols), (code['x0'] - 2 if code else None)


def _col(cols, x):
    name = None
    for x0, n in cols:
        if x >= x0:
            name = n
    return name


def _cells(cols, ws):
    """行の語を列に当てはめる。くっついた数（'-11.6-12.4'）は左端の列から右の列へ並べる"""
    out = {}
    names = [n for _, n in cols]
    for w in ws:
        parts = re.findall(r'-?\d+\.\d+', w['text'])
        if len(parts) >= 2 and ''.join(parts) == w['text']:
            # 数は右寄せなので、語の右端の列を最後の数の列とし、左へ順に並べる
            j = names.index(_col(cols, w['x1'] - 1))
            for k, p in enumerate(parts):
                out.setdefault(names[j - len(parts) + 1 + k], []).append(p)
        else:
            out.setdefault(_col(cols, w['x0']), []).append(w['text'])
    return out


def _meta(page):
    lines = [' '.join(w['text'] for w in ws) for _, ws in _rows(page.extract_words())]
    meta = {'raw_header': lines[:8]}
    meta['date'] = None
    for l in lines[:6]:
        t = l.split()
        if len(t) == 4 and re.fullmatch(r'[A-Z]{3}', t[0]) and t[1].isdigit() and re.fullmatch(r'\d{4}', t[3]):
            meta['date'] = f"{int(t[1])} {t[2]} {t[3]}"
    ev = next((l for l in lines[:4] if l.endswith('MOGULS')), '')
    meta['event'] = ("Men's Moguls" if ev.startswith("MEN'S") else "Ladies' Moguls" if ev.startswith("LADIES'")
                     else "Women's Moguls" if ev.startswith("WOMEN'S") else None)
    i = lines.index(ev) if ev in lines else None
    meta['round'] = lines[i + 1].title() if i is not None and i + 1 < len(lines) else None
    meta['codex'] = None
    judges, officials = [], []
    for l in lines:
        t = l.split()
        if len(t) >= 5 and t[0] == 'Judge' and t[2] in ('(Turns):', '(Air):'):
            k = next(j for j in range(3, len(t)) if re.fullmatch(r'[A-Z]{3}', t[j]) and j > 3)
            judges.append({'judge_no': int(t[1]), 'role': t[2][1:-2], 'name': ' '.join(t[3:k]), 'noc': t[k]})
        for lab in ('FIS Technical Delegate', 'Head Judge', 'Chief of Competition', 'Chief of Course'):
            if l.startswith(lab + ':'):
                t2 = l[len(lab) + 1:].split()
                k = next((j for j in range(1, len(t2)) if re.fullmatch(r'[A-Z]{3}', t2[j])), None)
                if k:
                    officials.append({'role': lab, 'name': ' '.join(t2[:k]), 'noc': t2[k]})
    meta['judges'] = judges
    meta['officials'] = officials
    text = '\n'.join(lines)

    def val(label, unit):
        m = re.search(re.escape(label) + r':?\s*(\d+(?:\.\d+)?)' + re.escape(unit), text)
        return float(m.group(1)) if m else None
    m = re.search(r'Number of Competitors:\s*(\d+)', text)
    meta['num_competitors'] = int(m.group(1)) if m else None
    meta['pace_time'] = val('Pace Time', 's')
    meta['course_length_m'] = val('Length', 'm')
    meta['course_width_m'] = val('Course Width', 'm')
    meta['gate_width_m'] = val('Gate Width', 'm')
    meta['gradient_deg'] = val('Gradient', '°')
    return meta


def parse_moguls_results(path, pages=None):
    """Returns (meta, records); 1 選手 1 記録"""
    records, problems = [], []
    with pdfplumber.open(path, pages=pages) as pdf:
        meta = _meta(pdf.pages[0])
        for page in pdf.pages:
            pno = page.page_number
            words = page.extract_words()
            cl = _columns(words)
            if cl is None:
                continue
            cols, noc_x = cl
            sec_top = next(w['top'] for w in words if w['text'] == 'Seconds')
            # 表の終わり: 表頭より下の署名欄（'Head Judge'）かページの下端（'FRM020901_73M'・女子は 'FRW…'）。審判団の欄の 'Head Judge:' は表頭より上
            end = min((w['top'] for w in words if w['top'] > sec_top and (w['text'] == 'Head' or re.fullmatch(r'FR[A-Z]\d{6}_\w+', w['text']))),
                      default=page.height)
            rows = [(t, ws) for t, ws in _rows(words) if sec_top + 8 < t < end - 1]
            k = 0
            while k < len(rows):
                top, ws = rows[k]
                fis = next((w for w in ws if re.fullmatch(r'\d{7}', w['text'])), None)
                if fis is None:
                    problems.append((pno, ' '.join(w['text'] for w in ws)))
                    k += 1
                    continue
                left = [w for w in ws if w['x1'] <= fis['x0']]
                ints = [w['text'] for w in left if w['text'].isdigit()]
                rank, bib = (int(ints[0]), int(ints[1])) if len(ints) == 2 else (None, int(ints[0]))
                name_ws = [w for w in ws if w['x0'] > fis['x1'] and (noc_x is None or w['x0'] < noc_x)]
                noc_w = next((w for w in ws if noc_x is not None and abs(w['x0'] - noc_x) < 6), None)
                noc = noc_w['text'] if noc_w else None
                if noc is None and name_ws and re.search(r'[a-z][A-Z]{3}$', name_ws[-1]['text']):
                    noc, name_ws[-1] = name_ws[-1]['text'][-3:], dict(name_ws[-1], text=name_ws[-1]['text'][:-3])
                yb_w = next((w for w in ws if re.fullmatch(r'(?:19|20)\d\d', w['text']) and w['x0'] > (noc_x or 0)), None)
                rec = {'rank': rank, 'bib': bib, 'fis_code': fis['text'], 'name': ' '.join(w['text'] for w in name_ws),
                       'noc': noc, 'yb': int(yb_w['text']) if yb_w else None, 'status': 'OK', 'reserve_judge': False,
                       'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None, 'base_scores': [],
                       'ded_scores': [], 'base_total': None, 'ded_total': None, 'turns_total': None, 'run_score': None,
                       'tie': None, 'q_block': None, 'best_score': None, 'counting': True,
                       'section': None, 'block_index': 0, 'run_label': None, 'page': pno}
                records.append(rec)
                right = [w for w in ws if yb_w and w['x0'] > yb_w['x1']]
                st = next((STATUS[w['text']] for w in right if w['text'] in STATUS), None)
                if st:
                    rec['status'], rec['rank'] = st, None
                    k += 1
                    if k < len(rows) and [w['text'] for w in rows[k][1]] == ['D:']:
                        k += 1
                    continue
                c1 = _cells(cols, right)
                c2 = _cells(cols, rows[k + 1][1]) if k + 1 < len(rows) else {}
                c3 = _cells(cols, rows[k + 2][1]) if k + 2 < len(rows) else {}
                if c1.get('label') != ['B:'] or c2.get('label') != ['D:']:
                    problems.append((pno, ' '.join(w['text'] for w in ws)))
                    k += 1
                    continue
                turn_cols = sorted({n for _, n in cols if re.fullmatch(r'turn\d', n)})
                air_cols = sorted({n for _, n in cols if re.fullmatch(r'air\d', n)})
                rec['seconds'] = _f(c1['sec'][0])
                rec['time_points'] = _f(c1['pts'][0])
                rec['base_scores'] = [_f(c1[n][0]) for n in turn_cols if n in c1]
                rec['base_total'] = _f(c1['turns_total'][0])
                rec['ded_scores'] = [_f(c2[n][0]) for n in turn_cols if n in c2]
                rec['ded_total'] = _f(c2['turns_total'][0])
                for c in (c1, c2):
                    rec['air_jumps'].append({'J6': _f(c[air_cols[0]][0]), 'J7': _f(c[air_cols[1]][0]),
                                             'jump': c['jump'][0], 'DD': _f(c['dd'][0])})
                rec['run_score'] = _f(c1['score'][0])
                tie = [t for t in c1.get('tie', []) if NUM.match(t)]
                rec['tie'] = _f(tie[0]) if tie else None
                if _f(c3.get('pts', ['nan'])[0]) == rec['time_points'] and c3.get('turns_total') and c3.get('air_total'):
                    rec['turns_total'] = _f(c3['turns_total'][0])
                    rec['air_total'] = _f(c3['air_total'][0])
                else:
                    problems.append((pno, ' '.join(w['text'] for w in rows[k + 2][1]) if k + 2 < len(rows) else '(合計の行が無い)'))
                k += 3
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
