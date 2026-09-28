"""FIS 標準の旧版（2014-15・2015-16。集計ソフト Mainstream / URTUR）のリザルトを表頭の語の位置で読む（方式 B）。

方式 A（old_a.py）が行の語の並び（DD の位置）で読むのに対し、こちらは表頭の各列の見出し語（J1〜J5・Total・J6・J7・
Jump・DD・Total・Time・Points・Score・Tie）の x 座標を基準に、数字の語を一番近い列に当てはめる。
2014-15（1 行目のターン合計の列に数字がある）と 2015-16（1 行目はベース点だけ、2 行目に減点とターン合計）を、
1 行目のターン合計の列に数字があるかで見分ける。
"""
import re
import pdfplumber

PARSER_VERSION = 'OB-1.0'

STATUS = {'DNF': 'DNF', 'DNS': 'DNS', 'DSQ': 'DSQ', 'DQ': 'DSQ'}
RE_NUM = re.compile(r'^-?\d+\.\d+$')
RE_DD = re.compile(r'^\d\.\d{3}$')


def _f(s):
    v = float(s)
    return 0.0 if v == 0 else v


def _lines(words):
    rows = {}
    for w in words:
        key = next((k for k in rows if abs(k - w['top']) < 2.0), w['top'])
        rows.setdefault(key, []).append(w)
    return [(k, sorted(v, key=lambda w: w['x0'])) for k, v in sorted(rows.items())]


def _split_glued(w):
    """くっついた審判点「2.42.6」を 2 語に分ける（座標は幅を等分）"""
    parts = re.findall(r'\d{1,2}\.\d', w['text'])
    if len(parts) < 2 or ''.join(parts) != w['text']:
        return [w]
    step = (w['x1'] - w['x0']) / len(parts)
    return [dict(w, text=p, x0=w['x0'] + i * step, x1=w['x0'] + (i + 1) * step) for i, p in enumerate(parts)]


def _strip_label(w):
    """総合の報告の走りの印（「F2:16.2」）を落とす"""
    m = re.match(r'^(?:F\d|Q\d?|SF|PH):(.*)$', w['text'])
    if not m:
        return [w]
    return [dict(w, text=m.group(1))] if m.group(1) else []


def _columns(lines):
    """表頭の 2 行（「Rank Bib Name YB Time Score Tie」と「Code Code J1 … Time」）と「Points」から列の基準 x を作る"""
    lower = next((ws for _, ws in lines if len(ws) > 6 and ws[0]['text'] == 'Code' and any(w['text'] == 'J1' for w in ws)), None)
    upper = next((ws for _, ws in lines if ws and ws[0]['text'] == 'Rank' and any(w['text'] == 'Bib' for w in ws)), None)
    pts = next((w for _, ws in lines for w in ws if w['text'] == 'Points'), None)
    if not lower or not upper or not pts:
        return None
    cx = lambda w: (w['x0'] + w['x1']) / 2
    cols = {}
    js = [w for w in lower if re.fullmatch(r'J\d', w['text'])]
    totals = [w for w in lower if w['text'] == 'Total']
    first_total = totals[0]
    turn_js = [w for w in js if w['x0'] < first_total['x0']]
    air_js = [w for w in js if w['x0'] > first_total['x0']]
    for i, w in enumerate(turn_js):
        cols[f't{i + 1}'] = cx(w)
    cols['ttot'] = cx(first_total)
    cols['a1'], cols['a2'] = cx(air_js[0]), cx(air_js[1])
    cols['jump'] = cx(next(w for w in lower if w['text'] == 'Jump'))
    cols['dd'] = cx(next(w for w in lower if w['text'] == 'DD'))
    cols['atot'] = cx(totals[1])
    cols['sec'] = cx(next(w for w in lower if w['text'] == 'Time'))
    cols['pts'] = cx(pts)
    cols['score'] = cx(next(w for w in upper if w['text'] == 'Score'))
    cols['tie'] = cx(next(w for w in upper if w['text'] == 'Tie'))
    ident = {'rank': upper[0]['x0'], 'yb': cx(next(w for w in upper if w['text'] == 'YB'))}
    codes = [w for w in lower if w['text'] == 'Code']
    ident['code_x1'] = codes[0]['x1'] + 30
    ident['noc'] = cx(codes[1]) if len(codes) > 1 else None
    return cols, ident, len(turn_js), max(w['top'] for w in [pts])


def _assign(words, cols):
    """数字・記号の語を一番近い列へ"""
    out = {}
    for w in words:
        c = (w['x0'] + w['x1']) / 2
        k = min(cols, key=lambda k: abs(cols[k] - c))
        out.setdefault(k, []).append(w['text'])
    return {k: ' '.join(v) for k, v in out.items()}


def _meta(text, words):
    toks = text.split()
    meta = {'raw_header': text.split('\n')[:8]}
    meta['date'] = None
    mon = {'JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'}
    for i, t in enumerate(toks):
        m = re.fullmatch(r'(?:[A-Z]{3})?(\d{1,2})', t)
        if m and i + 2 < len(toks) and toks[i + 1][:3] in mon and re.fullmatch(r'\d{4}', toks[i + 1][3:] or toks[i + 2]):
            year = toks[i + 1][3:] or toks[i + 2]
            meta['date'] = f"{int(m.group(1))} {toks[i + 1][:3]} {year}"
            break
    meta['event'] = next((l.strip() for l in text.split('\n') if l.strip() in ("Men's Moguls", "Ladies' Moguls", "Women's Moguls")
                          or re.match(r"(Men's|Ladies'|Women's) Moguls\b", l.strip())), None)
    if meta['event']:
        meta['event'] = re.match(r"(Men's|Ladies'|Women's) Moguls", meta['event']).group(0)
    meta['round'] = None
    meta['codex'] = next((toks[i + 1] for i, t in enumerate(toks[:-2]) if t == '/' and re.fullmatch(r'\d{4,5}', toks[i + 1])
                          and toks[i + 2] == 'Report'), None)
    judges = []
    for line in text.split('\n'):
        parts = line.split()
        if len(parts) >= 4 and parts[0] == 'Judge' and parts[1].isdigit() and parts[2] in ('(Turns):', '(Air):'):
            noc = parts[-1] if re.fullmatch(r'[A-Z]{3}', parts[-1]) else ''
            name = ' '.join(parts[3:-1] if noc else parts[3:])
            judges.append({'judge_no': int(parts[1]), 'role': parts[2][1:-2], 'name': name, 'noc': noc})
    meta['judges'] = judges
    officials = []
    for lab in ('FIS Technical Delegate', 'Head Judge', 'Chief of Competition', 'FIS Race Director', 'Chief of Course'):
        line = next((l for l in text.split('\n') if l.startswith(lab + ':')), None)
        if line:
            rest = line[len(lab) + 1:].split()
            cut = next((i for i, x in enumerate(rest) if x.endswith(':')), len(rest))
            rest = rest[:cut]
            # 同じ行の右側の見出し（「Length:」「Course Width:」「FIS Homologation Number:」）の語を落とす
            while rest and rest[-1] in ('Length', 'Course', 'Gate', 'Homologation', 'Width'):
                rest = rest[:-1]
                if rest and rest[-1] == 'FIS':
                    rest = rest[:-1]
            noc = rest[-1] if rest and re.fullmatch(r'[A-Z]{3}', rest[-1]) and len(rest) > 1 else ''
            name = ' '.join(rest[:-1] if noc else rest)
            if name:
                officials.append({'role': lab, 'name': name, 'noc': noc})
    meta['officials'] = officials

    def after(label, pat):
        i = text.find(label)
        m = re.match(r'\s*' + pat, text[i + len(label):]) if i >= 0 else None
        return m.group(1) if m else None
    nc = after('Number of Competitors:', r'(\d+)')
    meta['num_competitors'] = int(nc) if nc else None
    pt = after('Pace Time:', r'(\d+\.\d+)')
    meta['pace_time'] = float(pt) if pt else None
    v = after('Length:', r'(\d+(?:\.\d+)?)\s*m')
    meta['course_length_m'] = float(v) if v else None
    v = after('Course Width:', r'(\d+(?:\.\d+)?)\s*m')
    meta['course_width_m'] = float(v) if v else None
    v = after('Gate Width:', r'(\d+(?:\.\d+)?)\s*m')
    meta['gate_width_m'] = float(v) if v else None
    v = after('Gradient:', r'(\d+(?:\.\d+)?)')
    meta['gradient_deg'] = float(v) if v else None
    return meta


def parse_moguls_results(path):
    """Returns (meta, records); 1 選手 1 記録"""
    records, cur, stage, problems = [], None, None, []
    with pdfplumber.open(path) as pdf:
        pages = [(p.extract_words(), p.extract_text() or '') for p in pdf.pages]
    nj = 5
    for pno, (words, _) in enumerate(pages, start=1):
        lines = _lines(words)
        col = _columns(lines)
        if col is None:
            continue
        cols, ident, nj, top0 = col
        for top, ws in lines:
            if top <= top0 + 1:
                continue
            texts = [w['text'] for w in ws]
            low = [x.lower() for x in texts]
            if texts[0] in ('Head', 'NOTE', 'LEGEND') or ('report' in low and 'created' in low)                     or ('/' in texts and re.fullmatch(r'\d{1,2}', texts[0])):
                break  # フッタ（「24 JAN 2015 / … / 8107 Report created」「5 FEB 2015 / 8041」）・署名欄
            if all(re.fullmatch(r'[A-Z0-9]+', t) for t in texts) and len(texts) >= 2 and not any(t in STATUS for t in texts)                     and not any(re.fullmatch(r'\d{7}', t) for t in texts):
                continue  # 区切り「QUALIFIED TO FINAL 2」
            ws = [x for w in ws for y in _strip_label(w) for x in _split_glued(y)]
            code = next((w for w in ws if re.fullmatch(r'\d{7}', w['text']) or re.match(r'^\d{7}[A-Z]', w['text'])), None)
            if code is not None:
                left = [w for w in ws if w['x1'] <= code['x0'] + 0.5]
                rank = next((int(w['text']) for w in left if w['x0'] < ident['rank'] + 12 and w['text'].isdigit() and len(left) > 1), None)
                bib = int(left[-1]['text'])
                yb_w = next(w for w in ws if re.fullmatch(r'\d{4}', w['text']) and abs((w['x0'] + w['x1']) / 2 - ident['yb']) < 15)
                mid = [w for w in ws if w['x0'] > code['x0'] and w['x1'] <= yb_w['x0'] + 0.5 and w is not code]
                name_ws = list(mid)
                noc = None
                if name_ws and re.fullmatch(r'[A-Z]{3}', name_ws[-1]['text']):
                    noc = name_ws.pop()['text']
                elif name_ws and re.search(r'[a-z][A-Z]{3}$', name_ws[-1]['text']):
                    noc = name_ws[-1]['text'][-3:]  # 名前と国名がくっつく（「RohanAUS」）
                    name_ws[-1] = dict(name_ws[-1], text=name_ws[-1]['text'][:-3])
                glued = code['text'][7:]  # FIS コードと名前がくっつく（「2527488DZIEMIAN」）
                name = ' '.join(([glued] if glued else []) + [w['text'] for w in name_ws])
                cur = {'rank': rank, 'bib': bib, 'fis_code': code['text'][:7], 'name': name, 'noc': noc, 'yb': int(yb_w['text']),
                       'status': 'OK', 'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [],
                       'air_total': None, 'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None,
                       'turns_total': None, 'run_score': None, 'tie': None, 'q_block': None, 'best_score': None,
                       'counting': True, 'section': None, 'block_index': 0, 'run_label': None, 'page': pno}
                records.append(cur)
                rest = [w for w in ws if w['x0'] > yb_w['x1']]
                st = next((STATUS[w['text']] for w in rest if w['text'] in STATUS), None)
                if st:
                    cur['status'], cur['rank'], stage = st, None, 'status'
                    continue
                c = _assign(rest, cols)
                cur['base_scores'] = [_f(c[f't{i}']) for i in range(1, nj + 1) if f't{i}' in c]
                if c.get('ttot'):
                    cur['turns_total'], stage = _f(c['ttot']), 'single'
                else:
                    stage = 'bd'
                if c.get('jump'):
                    cur['air_jumps'].append({'J6': _f(c['a1']), 'J7': _f(c['a2']), 'jump': c['jump'], 'DD': _f(c['dd'])})
                cur['air_total'], cur['seconds'] = _f(c['atot']), _f(c['sec'])
                cur['time_points'], cur['run_score'] = _f(c['pts']), _f(c['score'])
                cur['tie'] = c['tie'] if c.get('tie') and c['tie'] != 'Q' else None
                continue
            if cur is None or stage is None:
                problems.append((pno, ' '.join(texts)))
                continue
            first_col = min(v for k, v in cols.items() if k.startswith('t'))
            name_frag = [w for w in ws if (w['x0'] + w['x1']) / 2 < first_col - 12 and re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\.]*", w['text'])]
            if name_frag:
                cur['name'] += ' ' + ' '.join(w['text'] for w in name_frag)
            ws = [w for w in ws if w not in name_frag]
            c = _assign(ws, cols)
            if stage == 'status':
                if all(float(v) == 0 for k, v in c.items() if RE_NUM.match(v)):
                    stage = None
                    continue
                problems.append((pno, ' '.join(texts)))
                continue
            if stage == 'bd':
                cur['ded_scores'] = [-abs(_f(c[f't{i}'])) if _f(c[f't{i}']) else 0.0 for i in range(1, nj + 1) if f't{i}' in c]
                cur['turns_total'] = _f(c['ttot']) if c.get('ttot') else None
            if c.get('jump'):
                cur['air_jumps'].append({'J6': _f(c['a1']), 'J7': _f(c['a2']), 'jump': c['jump'], 'DD': _f(c['dd'])})
            stage = None
    meta = _meta(pages[0][1] + '\n' + pages[-1][1], pages[0][0])
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
