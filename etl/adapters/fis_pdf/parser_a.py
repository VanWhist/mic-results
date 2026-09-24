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
PARSER_VERSION = 'A-2.3'
STATUS_WORDS = {'DNF', 'DNS', 'DSQ', 'DQ'}


def band(words, name):
    lo, hi = COLS[name]
    return [w for w in words if lo <= w['x0'] < hi]


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
                            'noc': noc_w[0]['text'] if noc_w else ''})
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


def _is_num(t):
    return bool(NUM_RE.fullmatch(t))


def _first_text(words, name, top, tol=2.0):
    ws = [w for w in band(words, name) if abs(w['top'] - top) < tol]
    ws.sort(key=lambda w: w['x0'])
    return ws[0]['text'] if ws else None


def _turns_scores(row_words, top_target):
    """J1..J5 raw texts on the line at top_target, splitting glued pairs like '-10.2-12.3'."""
    raw = []
    for name in ('J1', 'J2', 'J3', 'J4', 'J5'):
        ws = [w for w in band(row_words, name) if top_target is not None and abs(w['top'] - top_target) < 2]
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
                and abs(w['top'] - line1_top) < 2 and w['x0'] >= COLS['seconds'][0]]
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
    for t in air_tops:
        j6, j7 = _first_text(blk_words, 'J6', t), _first_text(blk_words, 'J7', t)
        jp, dd = _first_text(blk_words, 'jump', t), _first_text(blk_words, 'DD', t)
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
    bt = [w for w in tot_col if b_top is not None and abs(w['top'] - b_top) < 2]
    dt = [w for w in tot_col if d_top is not None and abs(w['top'] - d_top) < 2]
    tt = [w for w in tot_col if (b_top is None or abs(w['top'] - b_top) >= 2)
          and (d_top is None or abs(w['top'] - d_top) >= 2)]
    out['base_total'] = float(bt[0]['text']) if bt else None
    out['ded_total'] = float(dt[0]['text']) if dt else None
    out['turns_total'] = float(tt[0]['text']) if tt else None
    # The PDF leaves Deduction Total blank when every deduction is -0.0 (Almaty 2023 M F2).
    if out['ded_total'] is None and len(out['ded_scores']) == 5 and \
            all(x is not None for x in out['ded_scores']) and sum(out['ded_scores']) == 0:
        out['ded_total'] = 0.0

    score_band = 'block_score' if q_layout else 'run_score'
    rs = _first_text(blk_words, score_band, line1_top)
    out['run_score'] = float(rs) if rs and _is_num(rs) else None
    return out


def _parse_page_athletes(words, page_height, table_end, page_no):
    """Returns one record per athlete-block. table_end: top beyond which the table has
    no data on this page (the Jury section start on the last page)."""
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

    q_layout = any(re.fullmatch(r'Q[12]', w['text']) for w in band(words, 'q_label') if w['top'] < cutoff)

    rank_tops = sorted(w['top'] for w in band(words, 'rank') if w['top'] < cutoff and re.fullmatch(r'\d+', w['text']))
    # Un-ranked rows (DNF/DNS/DSQ): a bib number in the bib band with no rank on the same line.
    bib_tops = sorted(w['top'] for w in band(words, 'bib') if w['top'] < cutoff and re.fullmatch(r'\d+', w['text']))
    status_tops = [t for t in bib_tops if not any(abs(t - rt) < 2 for rt in rank_tops)]
    # Divider lines print non-digit text in the Rank column ("Qualified to Final 1", ...).
    divider_tops = sorted(set(w['top'] for w in band(words, 'rank')
                              if w['top'] < cutoff and w['top'] > 190 and not re.fullmatch(r'\d+', w['text'])))
    all_row_tops = sorted(set(rank_tops + status_tops + divider_tops))

    records = []
    for i, rtop in enumerate(all_row_tops):
        band_top = rtop - 3
        band_bot = (all_row_tops[i + 1] - 3) if i + 1 < len(all_row_tops) else cutoff
        row_words = [w for w in words if band_top <= w['top'] < band_bot]
        row_words.sort(key=lambda w: (w['top'], w['x0']))

        bib_txt = _first_text(row_words, 'bib', rtop)
        if bib_txt is None or not bib_txt.isdigit():
            continue  # divider / repeated header / stray word
        bib = int(bib_txt)
        fis_code = _first_text(row_words, 'fis_code', rtop)
        name_words = sorted([w for w in band(row_words, 'name') if abs(w['top'] - rtop) < 2], key=lambda w: w['x0'])
        # A long name wraps onto a second line inside the Name column ("GERKEN SCHOFIELD" /
        # "Makayla", "GORODKO" / "Anastassiya") ~9-10pt below the first line. No other column
        # prints in the Name band on that line, so append it.
        name_words2 = sorted([w for w in band(row_words, 'name') if 6 <= w['top'] - rtop < 15], key=lambda w: w['x0'])
        noc = _first_text(row_words, 'noc', rtop)
        # 'ElliotCAN': long first name glued to the NOC with no space; split when the last
        # name word ends in a 3-letter uppercase code and extends into the NOC column.
        if noc is None and name_words:
            last_w = name_words[-1]
            m = re.match(r'^(.{2,}?)([A-Z]{3})$', last_w['text'])
            if m and last_w['x1'] > COLS['name'][1]:
                sw = dict(last_w); sw['text'] = m.group(1)
                name_words = name_words[:-1] + [sw]
                noc = m.group(2)
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
                rec['best_score'] = best if j == 0 else None  # the counting score belongs to the athlete's first block
                if j > 0:
                    rec['tie'] = None  # tie-break points are printed once per athlete, on the first block
                rec['counting'] = (j == 0)  # first block is the round's own run
                records.append(rec)
        else:
            blk = _parse_block(row_words, rtop, band_bot, False)
            rec = dict(ident); rec.update(blk)
            rec['q_block'] = None
            rec['best_score'] = None
            rec['counting'] = True
            records.append(rec)
    return records


def parse_moguls_results(path):
    """Parses a FIS single-run moguls result PDF (Q / Q1 / Q2 / F1 / F2).
    Returns (meta, records); one record per athlete score block."""
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        first_words, first_text = pages[0].extract_words(), pages[0].extract_text() or ''
        jury_page_words, jury_page_text = None, None
        records = []
        for pno, page in enumerate(pages, start=1):
            words = page.extract_words()
            jury_words = [w for w in words if w['text'] == 'Jury']
            if jury_words:
                jury_page_words = words
                jury_page_text = page.extract_text() or ''
            table_end = jury_words[0]['top'] - 2 if jury_words else None
            records.extend(_parse_page_athletes(words, page.height, table_end, pno))
    meta = _extract_meta(first_words, first_text, jury_page_words, jury_page_text)
    meta['parser_version'] = PARSER_VERSION
    meta['q_layout'] = any(r['q_block'] for r in records)
    return meta, records


parse_moguls_final = parse_moguls_results


if __name__ == '__main__':
    meta, recs = parse_moguls_results(sys.argv[1])
    print(json.dumps({'meta': meta, 'records': recs}, indent=1, ensure_ascii=False))
