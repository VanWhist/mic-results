"""文字表（「=== ===」の下線がある等幅の表。カナダの集計ソフト Winfree）の FIS リザルトを列の位置で読む（方式 B）。

方式 A（ascii_a.py）が行の文字の並びを正規表現で読むのに対し、こちらは 1 文字ずつの座標を、表頭の下線「=== ===」の
各区間（列）に当てはめる。数字は列の右端にそろえて印字され、桁が列より長いときは左の空白にはみ出す
（「-11.0-33.7」のように隣の列とくっついて見える）ので、列と列の間の空白は右の列に入れる。
列の並び（21 列）: 順位 Bib 名前 FIS# 組 国 J1〜J5 ターン J6 J7 ジャンプ DD エア 審判点 秒 タイム点 得点。
"""
import re
import pdfplumber

PARSER_VERSION = 'AB-1.0'

COLS = ['no', 'bib', 'name', 'fis', 'grp', 'noc', 'j1', 'j2', 'j3', 'j4', 'j5', 'tl', 'j6', 'j7', 'jump', 'dd',
        'airs', 'judge', 'time', 'pts', 'run']
EN_MON = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
FR_MON = {'JANV': 'JAN', 'FÉVR': 'FEB', 'FÉV': 'FEB', 'MARS': 'MAR', 'AVR': 'APR', 'MAI': 'MAY', 'JUIN': 'JUN',
          'JUIL': 'JUL', 'AOÛT': 'AUG', 'SEPT': 'SEP', 'OCT': 'OCT', 'NOV': 'NOV', 'DÉC': 'DEC'}
FOOT_WORDS = ('Male', 'Mâle', 'Winfree', 'Filename:', 'Nom')


def _f(s):
    if s is None or s == '':
        return None
    v = float(s)
    return 0.0 if v == 0 else v


def _rows(chars):
    """文字を行（top の近いもの）にまとめる"""
    rows = {}
    for c in chars:
        key = next((k for k in rows if abs(k - c['top']) < 1.5), c['top'])
        rows.setdefault(key, []).append(c)
    return [sorted(v, key=lambda c: c['x0']) for _, v in sorted(rows.items())]


def _text(row):
    out, last = '', None
    for c in row:
        if last is not None and c['x0'] - last > 1.0:
            out += ' '
        out += c['text']
        last = c['x1']
    return out


def _spans(row):
    """下線の行 → 列の区間 [(x0, x1)]（'=' の連なり）"""
    spans, cur = [], None
    for c in row:
        if c['text'] != '=':
            continue
        if cur and c['x0'] - cur[1] < 1.0:
            cur[1] = c['x1']
        else:
            cur = [c['x0'], c['x1']]
            spans.append(cur)
    return spans


def _cells(row, spans):
    """1 行の文字を列に分ける。続いた文字（語）ごとに、語の右端が入る列（右寄せの数字。長い桁は左の空白にはみ出す）、
    無ければ左端が入る列（左寄せの文字。組の「F-1」は右にはみ出す）に入れる"""
    words, cur = [], None
    for c in row:
        # 数字の直後の「-」は次の数字の始まり（「-7.8-14.3」は J5 と合計がくっついたもの）
        glued_minus = c['text'] == '-' and cur and cur['text'][-1:].isdigit()
        if cur and c['x0'] - cur['x1'] <= 1.0 and not glued_minus:
            cur['text'] += c['text']
            cur['x1'] = c['x1']
        else:
            cur = {'text': c['text'], 'x0': c['x0'], 'x1': c['x1']}
            words.append(cur)
    cells = [[] for _ in spans]
    for w in words:
        k = next((i for i, (x0, x1) in enumerate(spans) if x0 - 0.5 < w['x1'] <= x1 + 0.5), None)
        if k is None:
            k = next((i for i, (x0, x1) in enumerate(spans) if x0 - 0.5 <= w['x0'] <= x1 + 0.5), None)
        if k is not None:
            cells[k].append(w['text'])
    return dict(zip(COLS, (' '.join(x) for x in cells)))


def _meta(page):
    """見出しの項目を、ラベルの語の位置から読む"""
    words = page.extract_words()
    by_top = {}
    for w in words:
        key = next((k for k in by_top if abs(k - w['top']) < 1.5), w['top'])
        by_top.setdefault(key, []).append(w)
    lines = [' '.join(w['text'] for w in sorted(v, key=lambda w: w['x0'])) for _, v in sorted(by_top.items())]
    meta = {'raw_header': lines[:8]}
    date_line = lines[2] if len(lines) > 2 else ''
    toks = date_line.replace(',', ' ').split()
    meta['date'] = None
    for i, t in enumerate(toks[:-2]):
        if t[:3].upper() in EN_MON and toks[i + 1].isdigit() and re.fullmatch(r'\d{4}', toks[i + 2]):
            meta['date'] = f"{int(toks[i + 1])} {t[:3].upper()} {toks[i + 2]}"
            break
        if t.isdigit() and toks[i + 1].rstrip('.').upper() in FR_MON and re.fullmatch(r'\d{4}', toks[i + 2]):
            meta['date'] = f"{int(t)} {FR_MON[toks[i + 1].rstrip('.').upper()]} {toks[i + 2]}"
            break
    last = toks[-1] if toks else ''
    meta['codex'] = last.strip('()') if re.fullmatch(r'\(\d{4,5}\)', last) else None
    sex = next((l.split()[0] for l in lines if l.split() and l.split()[0] in ('Female', 'Male', 'Femme', 'Homme')), None)
    fem = sex in ('Female', 'Femme')
    meta['event'] = ("Women's Moguls" if fem else "Men's Moguls") if sex else None
    judges, officials = [], []
    for w in words:
        m = re.fullmatch(r'(\d)\((T&L|Tech|Air|Saut)\):', w['text'])
        if m and any(x['text'] in ('Judge', 'Juge') and abs(x['top'] - w['top']) < 1.5 and x['x1'] <= w['x0'] + 1 for x in words):
            nxt = sorted([x for x in words if abs(x['top'] - w['top']) < 1.5 and x['x0'] > w['x1']], key=lambda x: x['x0'])
            if nxt:
                judges.append({'judge_no': int(m.group(1)), 'role': 'Turns' if m.group(2) in ('T&L', 'Tech') else 'Air',
                               'name': nxt[0]['text'].replace(',', ' ').strip(), 'noc': ''})
    judges.sort(key=lambda j: j['judge_no'])
    meta['judges'] = judges
    for role, labels in (('Head Judge', ('Head Judge:', 'Juge en Chef:')), ('Chief of Competition', ('Chief of Comp:', 'Chef de Comp:')),
                         ('Technical Delegate', ('T.D.:', 'D.T.:')), ('Chief of Scoring', ('Chief of Scoring:', 'Chef de Compilation:'))):
        for l in lines:
            hit = next((lab for lab in labels if l.startswith(lab)), None)
            if hit:
                rest = l[len(hit):].split()
                if rest:
                    officials.append({'role': role, 'name': rest[0].replace(',', ' ').strip(), 'noc': ''})
                break
    meta['officials'] = officials
    meta['pace_time'] = None
    for l in lines:
        m = re.search(r'(?:Male|Homme) = (\d+\.\d+), (?:Female|Femme) = (\d+\.\d+)', l)
        if m and ('Pace:' in l or 'Temps de base:' in l):
            meta['pace_time'] = float(m.group(2) if fem else m.group(1)) if sex else None
            break

    def after(labels, unit):
        for l in lines:
            for lab in labels:
                i = l.find(lab)
                if i >= 0:
                    m = re.match(r'\s*(\d+(?:\.\d+)?)\s*' + unit, l[i + len(lab):])
                    if m:
                        return float(m.group(1))
        return None
    meta['course_length_m'] = after(('Length:', 'Longueur:'), 'm')
    meta['course_width_m'] = after(('Width:', 'Largeur:'), 'm')
    meta['gate_width_m'] = None
    meta['gradient_deg'] = after(('Pitch:', 'Inclinaison:'), 'deg')
    co = after(('Cutoff:',), '')
    meta['cutoff'] = int(co) if co is not None else None
    meta['num_competitors'] = None
    return meta


def parse_moguls_results(path):
    """Returns (meta, records); 1 選手 1 記録"""
    records, cur, problems = [], None, []
    with pdfplumber.open(path) as pdf:
        meta = _meta(pdf.pages[0])
        for pno, page in enumerate(pdf.pages, start=1):
            rows = _rows([c for c in page.chars if c['text'].strip()])
            ui = next((i for i, r in enumerate(rows) if sum(1 for c in r if c['text'] == '=') > 40), None)
            if ui is None:
                continue
            spans = _spans(rows[ui])
            if len(spans) != len(COLS):
                problems.append((pno, f'下線の列が {len(spans)} 個（{len(COLS)} 個のはず）'))
                continue
            for row in rows[ui + 1:]:
                if _text(row).split()[0] in FOOT_WORDS:
                    break
                c = _cells(row, spans)
                if c['bib'].isdigit() and re.fullmatch(r'\d{7}', c['fis']):
                    cur = {'rank': int(c['no']) if c['no'].isdigit() else None, 'bib': int(c['bib']), 'fis_code': c['fis'],
                           'name': c['name'].replace(',', ' ').strip(), 'noc': c['noc'], 'yb': None, 'status': 'OK',
                           'reserve_judge': False, 'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None,
                           'base_scores': [], 'ded_scores': [], 'base_total': None, 'ded_total': None, 'turns_total': None,
                           'run_score': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True,
                           'section': None, 'block_index': 0, 'run_label': None, 'page': pno, '_line': 1}
                    records.append(cur)
                    flags = {v.lower() for k, v in c.items() if k not in ('name',)}
                    st = next((s for s, keys in (('DNF', ('dnf',)), ('DNS', ('dns',)), ('DSQ', ('dsq', 'dq'))) if flags & set(keys)), None)
                    if st:
                        cur['status'], cur['rank'], cur['_line'] = st, None, 0
                    else:
                        cur['base_scores'] = [_f(c[f'j{i}']) for i in range(1, 6)]
                        cur['base_total'] = _f(c['tl'])
                    continue
                if cur is None or cur['_line'] == 0:
                    problems.append((pno, _text(row)))
                    continue
                if cur['_line'] == 1 and c['j1'] and c['tl'] and not c['run']:
                    cur['ded_scores'] = [_f(c[f'j{i}']) for i in range(1, 6)]
                    cur['ded_total'] = _f(c['tl'])
                    cur['_line'] = 2
                    continue
                if cur['_line'] == 2 and c['tl'] and c['run']:
                    cur['turns_total'] = _f(c['tl'])
                    if c['jump']:
                        cur['air_jumps'].append({'J6': _f(c['j6']), 'J7': _f(c['j7']), 'jump': c['jump'], 'DD': _f(c['dd'])})
                    cur['air_total'], cur['seconds'], cur['run_score'] = _f(c['airs']), _f(c['time']), _f(c['run'])
                    cur['time_points'] = _f(c['pts']) if c['pts'] else 0.0  # タイム点 0 は空欄
                    cur['_line'] = 3
                    continue
                if cur['_line'] == 3 and c['j6'] and not c['tl']:
                    if c['jump']:
                        cur['air_jumps'].append({'J6': _f(c['j6']), 'J7': _f(c['j7']), 'jump': c['jump'], 'DD': _f(c['dd'])})
                    cur['_line'] = 0
                    continue
                problems.append((pno, _text(row)))
    for r in records:
        r.pop('_line', None)
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = False
    meta['unparsed_lines'] = problems
    return meta, records
