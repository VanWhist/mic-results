import pdfplumber, re, json, sys

# x0 column bands, derived from header word positions with a small buffer since
# pdfplumber's word x0 for data values can sit ~1-2pt left of the header's own x0.
COLS = {
    'rank': (25, 45), 'bib': (45, 65), 'fis_code': (65, 94), 'name': (94, 160),
    'noc': (160, 182), 'yb': (182, 222), 'seconds': (222, 257), 'time_points': (257, 280),
    'J6': (280, 297), 'J7': (297, 310), 'jump': (310, 339), 'DD': (339, 358),
    'air_total': (358, 378), 'bd_label': (378, 383),
    'J1': (383, 413), 'J2': (413, 430), 'J3': (430, 447), 'J4': (447, 464), 'J5': (464, 489),
    'turns_total_col': (486, 500), 'block_score': (500, 522), 'run_score': (526, 548), 'tie': (548, 585),
    'q_label': (198, 216),
}
PARSER_VERSION = 'A-2.6'
STATUS_WORDS = {'DNF', 'DNS', 'DSQ', 'DQ'}


_page_cols = None  # ページの表頭から決めた列の範囲（_page_layout）。無ければ COLS
_doc_cols = None  # 同じ PDF で最後に決められた列の範囲（表頭の一部しか取れないページで使う）


def band(words, name):
    lo, hi = (_page_cols or COLS)[name]
    return [w for w in words if lo <= w['x0'] < hi]


def _page_layout(words):
    """表頭（Rank の行の上下 14pt）の語の位置から、審判・得点の列の範囲を決める。
    W杯の PDF は COLS の固定位置で読めるが、世界ジュニア・NAC・ANC・ユニバーシアードは主催者の様式で
    ターン審判の人数（3/5）・列の間隔・Run Score の位置が違う（NAC 2022 は審判の間隔が広く Best Score 列がある、
    ANC 2026 は Run Score が x≈509）。データは表頭の語より数 pt 左に始まるので、範囲は表頭の x0 から少し左にとる。
    戻り値: (列の範囲, 表頭の下端 top)。表頭が見つからなければ (None, None)"""
    ranks = [w for w in words if w['text'] == 'Rank']
    if not ranks:
        return None, None
    rt = ranks[0]['top']
    hdr = sorted([w for w in words if abs(w['top'] - rt) < 14], key=lambda w: w['x0'])
    hdr_bottom = max(w['bottom'] for w in hdr)

    def first(text, after=0):
        return next((w['x0'] for w in hdr if w['text'] == text and w['x0'] > after), None)
    b_x = first('B', 370)
    name_x = first('Name')
    jump_x, dd_x = first('Jump', 200), first('DD', 200)
    js = [w for w in hdr if re.fullmatch(r'J\d', w['text'])]
    if b_x is None or jump_x is None or dd_x is None or not js:
        return None, hdr_bottom
    air = [w['x0'] for w in js if w['x0'] < jump_x]
    turns = [w['x0'] for w in js if w['x0'] > b_x]
    air_tot_x = first('Total', dd_x)
    turns_tot_x = first('Total', turns[-1]) if turns else None
    # Run Score の表頭は縦書きの「Sc」、「Score」、Best Score と重なった「ScoreScore」（ANC 2019・2024）
    sc = next((w['x0'] for w in hdr if w['text'] in ('Sc', 'Score', 'ScoreScore') and turns_tot_x and w['x0'] > turns_tot_x), None)
    if len(air) != 2 or not turns or None in (air_tot_x, turns_tot_x, sc):
        return None, hdr_bottom
    cols = dict(COLS)
    if name_x:
        # 姓の x0 が表頭「Name」より 2pt 左に出る様式がある（EC 2017 Prato Leventina は 93.9）
        cols['fis_code'] = (COLS['fis_code'][0], name_x - 4)
        cols['name'] = (name_x - 4, COLS['name'][1])
    # タイム点の数値が表頭より左に出る様式がある（NAC 2022 は x0=256.9）
    cols['seconds'] = (COLS['seconds'][0], 254)
    cols['time_points'] = (254, air[0] - 10)
    cols['J6'] = (air[0] - 10, air[1] - 10)
    cols['J7'] = (air[1] - 10, jump_x - 4)
    cols['jump'] = (jump_x - 4, dd_x - 12)
    cols['DD'] = (dd_x - 12, air_tot_x - 6)
    cols['air_total'] = (air_tot_x - 6, b_x - 4)
    cols['bd_label'] = (b_x - 4, turns[0] - 10)
    names = ['J1', 'J2', 'J3', 'J4', 'J5']
    for i, x in enumerate(turns):
        cols[names[i]] = (x - 10, (turns[i + 1] if i + 1 < len(turns) else turns_tot_x + 4) - 10)
    for n in names[len(turns):]:
        cols[n] = (-1, -1)  # この様式には無い審判の列
    cols['turns_total_col'] = (turns_tot_x - 6, sc - 4)
    # Run Score の列は幅 21pt（W杯 COLS と同じ）。NAC 2022 の Best Score（その右）は拾わない
    cols['run_score'] = (sc - 4, sc + 17)
    # Best Score 列（NAC 2022: 表頭の縦書き「Best Score」の B が Run Score の右、top が表頭より 3pt 上）
    # Best Score 列: 縦書きの B（NAC 2022）、「Best」（ANC 2019〜2024・世界ジュニア 2018）、2 つ目の「Score」（ANC 2018）
    best_x = next((w['x0'] for w in hdr if (w['text'] in ('B', 'Best') or (w['text'] == 'Score' and w['x0'] > sc + 10))
                   and w['x0'] > sc + 10), None)
    tie_x = first('Tie', sc)
    pts_x = next((w['x0'] for w in hdr if w['text'] in ('Race', 'Rac', 'Points') and w['x0'] > sc + 25), None)
    cols['best_col'] = (sc + 17, (tie_x or pts_x or sc + 45) - 4) if best_x else (-1, -1)
    if tie_x is None:
        # 表頭に Tie が無い様式（総合の報告: Run Score・Best Score・Race Points）。右端の数値は FIS ポイントで同点の値ではない
        cols['tie'] = (-1, -1)
    return cols, hdr_bottom


def _extract_meta(first_page_words, first_page_text, jury_page_words, jury_page_text=None):
    # Header layout differs between report families -- e.g. Olympic reports carry a
    # bilingual translation line after every English line (shifting everything down
    # by one line) and combine date+round on one line, while World Cup reports have
    # no translation lines and put date+start-time on one line with the round name on
    # a separate "Results <round>" line. Rather than assume a fixed line index (which
    # broke the very first time this hit a non-Olympic PDF), search the whole page's
    # text for each pattern so both families -- and future ones -- are more likely to
    # work without edits.
    lines = first_page_text.split('\n')
    meta = {'raw_header': lines[:8]}

    m = re.search(r'\b([A-Z]{3}\s+\d{1,2}\s+[A-Z]{3}\s+\d{4})\b', first_page_text)
    date_line = None
    if m:
        meta['date'] = m.group(1)
        date_line = next((l for l in lines if m.group(1) in l), None)

    m = re.search(r'Start Time:?\s*(\d{1,2}:\d{2})', first_page_text)
    if m:
        meta['start_time'] = m.group(1)

    if date_line:
        remainder = date_line.replace(meta['date'], '')
        remainder = re.sub(r'Start Time:?\s*\d{1,2}:\d{2}', '', remainder).strip()
        if remainder:
            meta['round'] = remainder  # Olympic style: date line ends with e.g. "Final 3"
    if 'round' not in meta:
        m = re.search(r'^Results\s+(\S.*)$', first_page_text, re.MULTILINE)
        if m:
            meta['round'] = m.group(1).strip()  # World Cup style: "Results Qualification"

    m = re.search(r"Men's Moguls|Ladies' Moguls|Women's Moguls", first_page_text)
    if m:
        meta['event'] = m.group(0)

    if lines and 'Freestyle' in lines[0] and not lines[0].startswith('FIS'):
        meta['venue'] = lines[0].split('Freestyle')[0].strip()  # Olympic style
    else:
        m = re.search(r"^([A-Z0-9À-Þ][A-Z0-9À-Þ'\.\- /]+\([A-Z]{3}\))$", first_page_text, re.MULTILINE)
        if m:
            meta['venue'] = m.group(1).strip()  # World Cup style: "ALPE D'HUEZ (FRA)"

    m = re.search(r"\([A-Z]{3}\)\s*/\s*(\d{3,5})\b", first_page_text)
    if m:
        meta['codex'] = m.group(1)

    words = jury_page_words or first_page_words
    judges, judge_rows = [], {}
    for w in words:
        if 300 <= w['x0'] < 326 and w['text'] == 'Judge':
            judge_rows[round(w['top'], 1)] = True
    for top in judge_rows:
        num_w = [w for w in words if 320 <= w['x0'] < 332 and abs(w['top'] - top) < 2]
        role_w = [w for w in words if 332 <= w['x0'] < 390 and abs(w['top'] - top) < 2]
        name_w = sorted([w for w in words if 396 <= w['x0'] < 522 and abs(w['top'] - top) < 2], key=lambda w: w['x0'])
        noc_w = [w for w in words if 522 <= w['x0'] < 545 and abs(w['top'] - top) < 2]
        if num_w and name_w:
            judges.append({'judge_no': int(num_w[0]['text']),
                            'role': role_w[0]['text'].strip('()') if role_w else '',
                            'name': ' '.join(w['text'] for w in name_w),
                            # 国名「N/A」（カナダの選考会 2019・2022）は国名不明（空）。旧版の様式と同じ扱い
                            'noc': noc_w[0]['text'] if noc_w and noc_w[0]['text'] != 'N/A' else ''})
    judges.sort(key=lambda j: j['judge_no'])
    meta['judges'] = judges

    # The Jury/Officials block's vertical position on the page varies a lot (it
    # starts right after however many result rows fit above it), so anchor on the
    # "Jury" section label itself rather than an absolute top range -- a fixed
    # range tuned for one report format came up empty on a different one.
    jury_tops = [w['top'] for w in words if w['text'] == 'Jury']
    first_judge_tops = sorted(w['top'] for w in words
                               if 300 <= w['x0'] < 326 and w['text'] == 'Judge')
    officials_top = jury_tops[0] + 5 if jury_tops else 0
    officials_bot = (first_judge_tops[0] - 2) if first_judge_tops else officials_top + 120

    officials, label_rows = [], {}
    for w in words:
        if 25 <= w['x0'] < 110 and officials_top <= w['top'] <= officials_bot:
            label_rows.setdefault(round(w['top'], 1), []).append(w)
    for top, ws in sorted(label_rows.items()):
        label = ' '.join(w['text'] for w in sorted(ws, key=lambda w: w['x0']))
        name_w = sorted([w for w in words if 140 <= w['x0'] < 270 and abs(w['top'] - top) < 2], key=lambda w: w['x0'])
        noc_w = [w for w in words if 270 <= w['x0'] < 298 and abs(w['top'] - top) < 2]
        if name_w:
            officials.append({'role': label, 'name': ' '.join(w['text'] for w in name_w),
                               'noc': noc_w[0]['text'] if noc_w else ''})
    meta['officials'] = officials

    tech = jury_page_text or first_page_text
    m = re.search(r'Number of Competitors:\s*(\d+)', first_page_text)
    meta['num_competitors'] = int(m.group(1)) if m else None
    m = re.search(r'Pace\s*Time[:\s]+(\d+\.\d+)', tech)
    meta['pace_time'] = float(m.group(1)) if m else None
    m = re.search(r'Length\s+(\d+(?:\.\d+)?)\s*m', tech)
    meta['course_length_m'] = float(m.group(1)) if m else None
    m = re.search(r'Gate Width\s+(\d+(?:\.\d+)?)\s*m\s*/\s*(\d+(?:\.\d+)?)\s*m', tech)
    meta['course_width_m'] = float(m.group(1)) if m else None
    meta['gate_width_m'] = float(m.group(2)) if m else None
    m = re.search(r'Gradient\s+(\d+(?:\.\d+)?)\s*°', tech)
    meta['gradient_deg'] = float(m.group(1)) if m else None
    return meta




# ---------------------------------------------------------------------------
# Row / block parsing.
#
# Two page layouts exist:
#   standard : one score block per athlete row; Run Score printed at x0~530.
#   q-layout : "Qualification 2" style reports (OWG / WSC / Championship format). Each
#              athlete row holds one or two score blocks labelled Q1 / Q2 at x0~206.
#              Every block has its own Run Score at x0~508, and the counting (best)
#              score is printed once at x0~530 on the row's first line. Athletes who
#              qualified directly from Q1 have a single block labelled Q1.
# A page is treated as q-layout when any Q1/Q2 label word sits in the q_label band.
# ---------------------------------------------------------------------------

NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')
LINE_TOL = 3.0  # B:/D: の印字と同じ行の数値の top の差の許容


def _is_num(t):
    return bool(NUM_RE.fullmatch(t))


def _first_word_x(words, name, top, tol=2.0):
    ws = sorted([w for w in band(words, name) if abs(w['top'] - top) < tol], key=lambda w: w['x0'])
    return ws[0]['x0'] if ws else None


def _first_text(words, name, top, tol=2.0):
    ws = [w for w in band(words, name) if abs(w['top'] - top) < tol]
    ws.sort(key=lambda w: w['x0'])
    return ws[0]['text'] if ws else None


def _turns_scores(row_words, top_target):
    """J1..J5 raw texts on the line at top_target, splitting glued pairs like '-10.2-12.3'."""
    raw = []
    names = [n for n in ('J1', 'J2', 'J3', 'J4', 'J5') if (_page_cols or COLS)[n][1] > 0]
    for name in names:
        ws = [w for w in band(row_words, name) if top_target is not None and abs(w['top'] - top_target) < LINE_TOL]
        raw.append(ws[0]['text'] if ws else None)
    for i, txt in enumerate(raw):
        if txt is None:
            continue
        m = re.fullmatch(r'(-?\d+\.\d+)(-\d+\.\d+)', txt)
        if m:
            raw[i] = m.group(1)
            if i + 1 < len(raw) and raw[i + 1] is None:
                raw[i + 1] = m.group(2)
    return [float(t) if t is not None else None for t in raw]


def _parse_block(row_words, line1_top, band_bot, q_layout):
    """Parse one score block whose first line is at line1_top. Returns a dict of score
    fields (all None when the block is a DNF/DNS/DSQ block)."""
    blk_words = [w for w in row_words if line1_top - 3 <= w['top'] < band_bot]
    status_w = [w['text'] for w in blk_words if w['text'] in STATUS_WORDS
                and abs(w['top'] - line1_top) < 2 and w['x0'] >= (_page_cols or COLS)['seconds'][0]]
    out = {'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None,
           'base_scores': [], 'base_total': None, 'ded_scores': [], 'ded_total': None,
           'turns_total': None, 'run_score': None, 'status': 'OK'}
    if status_w:
        out['status'] = status_w[0]
        return out

    sec, tp = _first_text(blk_words, 'seconds', line1_top), _first_text(blk_words, 'time_points', line1_top)
    out['seconds'] = float(sec) if sec else None
    out['time_points'] = float(tp) if tp else None

    # air lines: every distinct top in the J6 band within this block
    air_tops = sorted(set(round(w['top'], 1) for w in band(blk_words, 'J6')))
    cols = _page_cols or COLS
    for t in air_tops:
        j6, j7 = _first_text(blk_words, 'J6', t), _first_text(blk_words, 'J7', t)
        # ジャンプ記号と DD は J7 の右からエア合計の左までの語を左から順に読む（記号の幅で位置が揺れ、
        # NAC 2023 の「3」は x0=330 で DD の範囲に入る）。最後の小数（2〜3 桁）が DD、その前が記号
        # 記号の左端が J7 の範囲の終わりより少し左に出ることがある（ANC 2019「10oGA」x0=311.9）。J7 の数値はさらに左で終わる
        mid = sorted([w for w in blk_words if abs(w['top'] - t) < 2 and cols['J7'][1] - 4 <= w['x0'] < cols['air_total'][0]
                      and w['x0'] > (_first_word_x(blk_words, 'J7', t) or 0)],
                     key=lambda w: w['x0'])
        dd_i = max((i for i, w in enumerate(mid) if re.fullmatch(r'-?\d\.\d{2,3}', w['text'])), default=None)
        jp = mid[dd_i - 1]['text'] if dd_i else None
        dd = mid[dd_i]['text'] if dd_i is not None else None
        # DD がマイナス（-1.000）のジャンプは点を差し引く（世界ジュニア 2018 CROZET の「Lg -1.000」: 印字のエア合計 1.74 は
        # これを差し引いた値）。DD 0 の「N 0.000」「NJ 0.000」はジャンプなしの印字で、0 点の欄としてそのまま持つ
        if None not in (j6, j7, jp, dd) and _is_num(j6) and _is_num(j7) and _is_num(dd):
            out['air_jumps'].append({'J6': float(j6), 'J7': float(j7), 'jump': jp, 'DD': float(dd)})
    at = [w for w in band(blk_words, 'air_total') if _is_num(w['text'])]
    out['air_total'] = float(sorted(at, key=lambda w: w['top'])[0]['text']) if at else None

    bd = band(blk_words, 'bd_label')
    b_top = next((w['top'] for w in bd if w['text'] == 'B:'), None)
    d_top = next((w['top'] for w in bd if w['text'] == 'D:'), None)
    out['base_scores'] = _turns_scores(blk_words, b_top)
    out['ded_scores'] = _turns_scores(blk_words, d_top)

    tot_col = [w for w in band(blk_words, 'turns_total_col') if _is_num(w['text'])]
    bt = [w for w in tot_col if b_top is not None and abs(w['top'] - b_top) < LINE_TOL]
    dt = [w for w in tot_col if d_top is not None and abs(w['top'] - d_top) < LINE_TOL]
    tt = [w for w in tot_col if (b_top is None or abs(w['top'] - b_top) >= LINE_TOL)
          and (d_top is None or abs(w['top'] - d_top) >= LINE_TOL)]
    out['base_total'] = float(bt[0]['text']) if bt else None
    out['ded_total'] = float(dt[0]['text']) if dt else None
    out['turns_total'] = float(tt[0]['text']) if tt else None
    # The PDF leaves Deduction Total blank when every deduction is -0.0 (Almaty 2023 M F2).
    # 数える審判（5 人なら最高・最低を除く 3 人）の減点の合計が 0 のときも空欄（Park City 2018 の -0.0 -0.0 -0.0 -0.2 -0.0）。
    # 空欄は 0 として持ち、0 で正しいかは第 2 層の再計算で確かめる
    if out['ded_total'] is None and len(out['ded_scores']) >= 3 and \
            all(x is not None for x in out['ded_scores']):
        out['ded_total'] = 0.0

    score_band = 'block_score' if q_layout else 'run_score'
    rs = _first_text(blk_words, score_band, line1_top)
    out['run_score'] = float(rs) if rs and _is_num(rs) else None
    return out


RE_SECTION = re.compile(r'^(Super Final|Final ?\d?|Qualification ?\d?)$', re.I)


def _run_label(words, btop, bbot):
    """走りの印（F2: / F1 / Q1: など）。1 行目に出る様式（ANC 2018）と 2 行目に出る様式（ANC 2019 の総合）がある"""
    cand = sorted((w for w in words if btop - 2 < w['top'] < bbot and 196 <= w['x0'] < 228
                   and re.fullmatch(r'(?:F\d|Q\d|SF|PH):?', w['text'])), key=lambda w: w['top'])
    return cand[0]['text'].rstrip(':') if cand else None


def _parse_page_athletes(words, page_height, table_end, page_no, section=None):
    """Returns one record per athlete-block. table_end: top beyond which the table has
    no data on this page (the Jury section start on the last page)."""
    global _page_cols, _doc_cols
    _page_cols, hdr_bottom = _page_layout(words)
    if _page_cols is None and hdr_bottom is not None and _doc_cols is not None:
        _page_cols = _doc_cols  # 続きのページで表頭の一部が取れない（世界ジュニア 2022 の総合）: 前のページと同じ列
    if _page_cols is not None:
        _doc_cols = _page_cols
    if hdr_bottom is not None:
        # 表頭より上（大会名・日付の行）は選手の行ではない（ユニバーシアード 2025 の「TUE 14 JAN 2025 Results」）
        words = [w for w in words if w['top'] > hdr_bottom]
        # 審判団（Jury）の欄が表の上にある様式（NAC・ユニバーシアード）では、Jury の位置は表の終わりではない
        if table_end is not None and table_end < hdr_bottom:
            table_end = None
    # Footer: anchor on the 'Report created' / FIS-URL line, then include the nearby
    # '<date> / <venue> / <codex>' and 'Page x/y' lines (Olympic-style reports print the
    # date/venue line ABOVE the Report line; restricting '/' to the anchor's neighbourhood
    # keeps a '/' in a header venue name such as 'ST. MORITZ / ENGADIN' from matching).
    anchor_tops = [w['top'] for w in words if w['text'] in ('Report', 'www.fis-ski.com')]
    footer_tops = list(anchor_tops)
    if anchor_tops:
        base = min(anchor_tops)
        footer_tops += [w['top'] for w in words if w['text'] in ('Page', '/') and base - 16 <= w['top'] <= base + 40]
    footer_limit = min(footer_tops) - 2 if footer_tops else page_height - 40
    cutoff = min(table_end, footer_limit) if table_end is not None else footer_limit

    # Q1/Q2 の 2 ブロックの表（五輪・世界選手権の Q2 報告）には Q2 のラベルがある。ANC 2018 の総合は各行に走行の印（F1・Q1）が
    # 同じ位置に出るだけなので、Q2 が無ければ q-layout ではない
    q_layout = any(w['text'] == 'Q2' for w in band(words, 'q_label') if w['top'] < cutoff)

    rank_tops = sorted(w['top'] for w in band(words, 'rank') if w['top'] < cutoff and re.fullmatch(r'\d+', w['text']))
    # Un-ranked rows (DNF/DNS/DSQ): a bib number in the bib band with no rank on the same line.
    bib_tops = sorted(w['top'] for w in band(words, 'bib') if w['top'] < cutoff and re.fullmatch(r'\d+', w['text']))
    status_tops = [t for t in bib_tops if not any(abs(t - rt) < 2 for rt in rank_tops)]
    # Divider lines print non-digit text in the Rank column ("Qualified to Final 1", ...).
    divider_tops = sorted(set(w['top'] for w in band(words, 'rank')
                              # 表頭より上は取り除いてあるので、表頭が分かるページはページ上部の区切り（「Final 2」）も拾う
                              if w['top'] < cutoff and (hdr_bottom is not None or w['top'] > 190)
                              and not re.fullmatch(r'\d+', w['text'])))
    all_row_tops = sorted(set(rank_tops + status_tops + divider_tops))

    records = []
    # ページの先頭（表頭の直下、最初の選手の行より上）にある走り: 前のページの最後の選手の続き（総合の報告で、決勝に進んだ
    # 選手の走りがページをまたぐ。NAC 2019 Apex の松田 颯など）。呼び出し側で前の選手に付ける
    carry = []
    first_row = min((x for x in all_row_tops if x not in divider_tops), default=None)
    if hdr_bottom is not None and first_row is not None:
        head_words = [w for w in words if w['top'] < first_row - 3]
        cols0 = _page_cols or COLS
        cstarts = sorted({round(w['top'], 1) for w in head_words
                          if (w['text'] == 'B:' and cols0['bd_label'][0] <= w['x0'] < cols0['bd_label'][1])
                          or (w['text'] in STATUS_WORDS and w['x0'] >= cols0['seconds'][0])})
        for j, btop in enumerate(cstarts):
            bbot = (cstarts[j + 1] - 3) if j + 1 < len(cstarts) else first_row - 3
            blk = _parse_block(head_words, btop, bbot, False)
            blk['run_label'] = _run_label(head_words, btop, bbot)
            blk['page'] = page_no
            carry.append(blk)
    for i, rtop in enumerate(all_row_tops):
        band_top = rtop - 3
        band_bot = (all_row_tops[i + 1] - 3) if i + 1 < len(all_row_tops) else cutoff
        row_words = [w for w in words if band_top <= w['top'] < band_bot]
        row_words.sort(key=lambda w: (w['top'], w['x0']))
        if rtop in divider_tops:
            # 区切りの行。総合（OVERALL）の報告は「Final 2」「Final 1」「Qualification」の区切りごとに、その段で終わった選手を並べる。
            # 「Final 2」の 2 を BIB と読まないよう、区切りの行は選手の行として扱わない
            line = ' '.join(w['text'] for w in sorted([w for w in row_words if abs(w['top'] - rtop) < 2], key=lambda w: w['x0']))
            if RE_SECTION.match(line.strip()):
                section = re.sub(r'\s+', ' ', line.strip()).title()
            continue

        bib_txt = _first_text(row_words, 'bib', rtop)
        if bib_txt is None or not bib_txt.isdigit():
            continue  # divider / repeated header / stray word
        bib = int(bib_txt)
        fis_code = _first_text(row_words, 'fis_code', rtop)
        name_words = sorted([w for w in band(row_words, 'name') if abs(w['top'] - rtop) < 2], key=lambda w: w['x0'])
        # A long name wraps onto a second line inside the Name column ("GERKEN SCHOFIELD" /
        # "Makayla", "GORODKO" / "Anastassiya") ~9-10pt below the first line. No other column
        # prints in the Name band on that line, so append it.
        # 3 行に折り返す名前がある（EC 2017「RYKKE / ALMENNINGEN Ole / Andre」）ので行の順、行の中は左から
        name_words2 = sorted([w for w in band(row_words, 'name') if 6 <= w['top'] - rtop < 15],
                             key=lambda w: (round(w['top']), w['x0']))
        noc = _first_text(row_words, 'noc', rtop)
        # 'ElliotCAN': long first name glued to the NOC with no space; split when the last
        # name word ends in a 3-letter uppercase code and extends into the NOC column.
        if noc is None and name_words:
            last_w = name_words[-1]
            m = re.match(r'^(.{2,}?)([A-Z]{3})$', last_w['text'])
            if m and last_w['x1'] > (_page_cols or COLS)['name'][1]:
                sw = dict(last_w); sw['text'] = m.group(1)
                name_words = name_words[:-1] + [sw]
                noc = m.group(2)
        # 国名コードが途中で切れて印字される（Calgary 2018「DESMARAIS-GILBERTC」「AN」）: 名前の最後の大文字 1 字を国名へ戻す
        if noc and len(noc) == 2 and name_words and re.fullmatch(r'.{2,}[a-zA-Z\-][A-Z]', name_words[-1]['text']) \
                and name_words[-1]['text'][-2] in '-' + 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and name_words[-1]['x1'] > (_page_cols or COLS)['name'][1] - 20:
            lw = dict(name_words[-1]); noc = lw['text'][-1] + noc; lw['text'] = lw['text'][:-1]
            name_words = name_words[:-1] + [lw]
        name = ' '.join(w['text'] for w in name_words + name_words2)
        yb_txt = _first_text(row_words, 'yb', rtop)
        yb = int(yb_txt) if yb_txt and yb_txt.isdigit() else None
        rank_txt = _first_text(row_words, 'rank', rtop)
        rank = int(rank_txt) if rank_txt and rank_txt.isdigit() else None
        tie_w = band(row_words, 'tie')
        tie_nums = [w['text'] for w in tie_w if _is_num(w['text'])]
        tie = float(tie_nums[0]) if tie_nums else None
        reserve_judge = any(w['text'] == 'RES' for w in tie_w)
        ident = {'rank': rank, 'bib': bib, 'fis_code': fis_code, 'name': name, 'noc': noc, 'yb': yb,
                 'tie': tie, 'reserve_judge': reserve_judge, 'page': page_no}

        if q_layout:
            labels = sorted([w for w in band(row_words, 'q_label') if re.fullmatch(r'Q[12]', w['text'])],
                            key=lambda w: w['top'])
            best_txt = _first_text(row_words, 'run_score', rtop)
            best = float(best_txt) if best_txt and _is_num(best_txt) else None
            if not labels:  # no block label at all: treat as a single unlabeled block
                labels = [{'text': None, 'top': rtop}]
            for j, lw in enumerate(labels):
                blk_bot = labels[j + 1]['top'] - 3 if j + 1 < len(labels) else band_bot
                blk = _parse_block(row_words, lw['top'], blk_bot, True)
                rec = dict(ident); rec.update(blk)
                rec['q_block'] = lw['text']
                rec['section'] = section
                rec['best_score'] = best if j == 0 else None  # the counting score belongs to the athlete's first block
                if j > 0:
                    rec['tie'] = None  # tie-break points are printed once per athlete, on the first block
                rec['counting'] = (j == 0)  # first block is the round's own run
                records.append(rec)
        else:
            # 総合（OVERALL）の報告は、決勝に進んだ選手の行に決勝 2・決勝 1・予選の走りを続けて印字する（各走りは 3 行、
            # 1 行目に B:。走りの印「F2:」「F1」「Q1:」が付く様式と付かない様式がある）。走りの 1 行目の位置で分ける
            cols = _page_cols or COLS
            starts = sorted({round(w['top'], 1) for w in row_words
                             if (w['text'] == 'B:' and cols['bd_label'][0] <= w['x0'] < cols['bd_label'][1])
                             or (w['text'] in STATUS_WORDS and w['x0'] >= cols['seconds'][0])})
            blocks = []
            for s in starts:
                if not blocks or s - blocks[-1] >= 5:
                    blocks.append(s)
            if not blocks or abs(blocks[0] - rtop) >= 2:
                blocks = [rtop] + [s for s in blocks if abs(s - rtop) >= 2]
            for j, btop in enumerate(blocks):
                bbot = (blocks[j + 1] - 3) if j + 1 < len(blocks) else band_bot
                blk = _parse_block(row_words, btop, bbot, False)
                rec = dict(ident); rec.update(blk)
                rec['run_label'] = _run_label(row_words, btop, bbot)
                rec['block_index'] = j
                rec['q_block'] = None
                rec['section'] = section
                rec['best_score'] = None
                if j == 0 and cols.get('best_col', (-1, -1))[1] > 0:
                    bs = _first_text(row_words, 'best_col', btop)
                    rec['best_score'] = float(bs) if bs and _is_num(bs) else None
                if j > 0:
                    rec['rank'] = None  # 順位と同点の値は選手の 1 行目（最後の走り）にだけ印字される
                    rec['tie'] = None
                rec['counting'] = True
                records.append(rec)
    return records, section, carry


def parse_moguls_results(path, pages=None):
    """Parses a FIS single-run moguls result PDF (Q / Q1 / Q2 / F1 / F2).
    Returns (meta, records); one record per athlete score block.
    pages: そのラウンドの報告書のページ（1 始まりの番号の list）。1 つの PDF に複数ラウンドが綴じてある大会（2017 札幌アジア大会）。
    ページ番号は PDF 全体での番号のまま（page.page_number）"""
    global _doc_cols
    _doc_cols = None
    with pdfplumber.open(path, pages=pages) as pdf:
        pages = pdf.pages
        first_words, first_text = pages[0].extract_words(), pages[0].extract_text() or ''
        jury_page_words, jury_page_text = None, None
        records = []
        section = None  # 総合の報告の区切り（ページをまたいで続く）
        for page in pages:
            pno = page.page_number
            words = page.extract_words()
            jury_words = [w for w in words if w['text'] == 'Jury']
            if jury_words:
                jury_page_words = words
                jury_page_text = page.extract_text() or ''
            table_end = jury_words[0]['top'] - 2 if jury_words else None
            recs, section, carry = _parse_page_athletes(words, page.height, table_end, pno, section)
            if carry and records:
                last = records[-1]
                for k, blk in enumerate(carry, start=1):
                    rec = {x: last.get(x) for x in ('bib', 'fis_code', 'name', 'noc', 'yb', 'reserve_judge', 'section')}
                    rec.update(blk)
                    rec.update({'rank': None, 'tie': None, 'q_block': None, 'best_score': None, 'counting': True,
                                'block_index': (last.get('block_index') or 0) + k})
                    records.append(rec)
            records.extend(recs)
    meta = _extract_meta(first_words, first_text, jury_page_words, jury_page_text)
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = any(r['q_block'] for r in records)
    return meta, records


parse_moguls_final = parse_moguls_results


if __name__ == '__main__':
    meta, recs = parse_moguls_results(sys.argv[1])
    print(json.dumps({'meta': meta, 'records': recs}, indent=1, ensure_ascii=False))
