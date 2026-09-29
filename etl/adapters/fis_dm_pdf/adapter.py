"""fis_dm_pdf アダプタ: FIS 海外大会のデュアルモーグル最終成績（順位のみ、tier rank）。

国内の saj_dm と同じく、最終成績の表（報告書 RLF）から順位・Bib・FIS コード・名前・国・生年・対戦経過・最終段だけを持つ。
得点や審判点は持たない（デュアルモーグルは順位のみで公開する方針）。
対応する様式（FIS 標準。2016-17 以降の Mainstream / URTUR / Swiss Timing の集計ソフト）:
  表頭 "Rank Bib Name [NSA] YB Progression"（FIS コード・国の列の見出しは 2 行目）
  段の見出し（Big Final / Small Final / Quarter Final / Eight Final / 1/8 Final / Round of 32 …、くっついて "BigFinal" のことも）
  1 選手 = 1 行目「順位 Bib FISコード 名前 国 生年 対戦経過…」＋ 対戦経過や名前の折り返しの行
順位の無い行（途中棄権で順位が付かない選手）は順位なしで持つ。
"""
import os, re
import pdfplumber
from ... import config
from ...verify import Finding

PARSER_VERSION = 'FIS-DM-1.0'
MONTHS = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
# 1 行目: [順位] Bib FISコード 名前… 国 生年 [対戦経過]
# 順位の後ろに点が付く様式がある（「1. 7 2529403 …」、2017-19 の NAC・世界ジュニア）。3 桁の Bib が「…」で切れて
# 順位とくっつくことがある（「10.11… 2527725」= 10 位・Bib 11…、「10… 2532366」= 順位なし・Bib 10…）。行頭に「DNS」が付くことがある。
# 名前と国名がくっつくことがある（「PASCARELLA RiccardoSUI」）
RE_ROW = re.compile(r'^(?:(DNS|DNF|DSQ)\s+)?(?:(\d+)(?:\.\s*|\s+))?(\d+)(…?)\s+(\d{7})\s*(.+?)\s*([A-Z]{3})\s+((?:19|20)\d\d)\b\s*(.*)$')
RE_ROW_NOYB = re.compile(r'^(\d+)\s+(\d+)\s+(\d{7})\s+(.+?)\s+([A-Z]{3})(?:\s+(DNF|DNS|DSQ))?$')
RE_STAGE = re.compile(r'^(Big ?Final|Small ?Final|Semi ?Finals?|Quarter ?Finals?|Eight ?Finals?|1/\d+ ?Finals?|Round ?of ?\d+'
                      r'|Not ?Ranked|Qualification(?: ?Heat ?Round)?|Did ?Not ?(?:Start|Finish)|Disqualified)$', re.I)
# 対戦経過の印（R32-1: / EF-3: / QF-2: / SF-1: / F: / SmF: / Big F: / BigF:）
RE_PROG = re.compile(r'(?:\b(?:R\d+|EF|QF|SF|SmF|BigF|SmallF|Big F|Small F|F)-?\d*:)')
# 対戦経過が「Tot: 20.00, Rk 1」の途中で折り返した行（世界ジュニア 2021）
RE_PROG_TAIL = re.compile(r'^(?:[RB],\s*)?(?:Tot:\s*)?(?:\d+(?:\.\d+)?,\s*)?Rk\s*\d+')
# 表の中に出る表の外の行（天気・凡例・曜日つきの日付のフッタ）
RE_SKIP = re.compile(r'^(Weather:|Legend:|Conditions on Course|[A-Z]{3} \d{1,2} [A-Z]{3} \d{4} /|\d{2}-\d{2}-\d{4}\s*/|Data Processing|\d{1,2}$'
                     r'|F\d+ - |.*changed to follow rule)')  # 天気の欄・凡例・フッタ・前走者・審判点の訂正の注記（ユニバーシアード 2017）
STATUS_WORDS = ('DNF', 'DNS', 'DSQ', 'DQ')


def stage_name(s):
    """段の見出しの表記をそろえる（「BigFinal」「BIG FINAL」→「Big Final」、「Roundof32」→「Round of 32」、「1/8 Finals」→「1/8 Final」）"""
    k = re.sub(r'\s+', '', s).lower()
    fixed = {'bigfinal': 'Big Final', 'smallfinal': 'Small Final', 'semifinal': 'Semi Final', 'semifinals': 'Semi Final',
             'quarterfinal': 'Quarter Final', 'quarterfinals': 'Quarter Final', 'eightfinal': 'Eight Final',
             'eightfinals': 'Eight Final', 'notranked': 'Not Ranked', 'qualification': 'Qualification',
             'qualificationheatround': 'Qualification Heat Round', 'didnotstart': 'Did Not Start',
             'didnotfinish': 'Did Not Finish', 'disqualified': 'Disqualified'}
    if k in fixed:
        return fixed[k]
    m = re.fullmatch(r'roundof(\d+)', k)
    if m:
        return f'Round of {m.group(1)}'
    m = re.fullmatch(r'1/(\d+)finals?', k)
    if m:
        return f'1/{m.group(1)} Final'
    return s.strip()


def parse_pdf(path):
    meta = {'gender': None, 'date': None, 'num_competitors': None, 'title': None}
    athletes, stage, cur, in_table, problems = [], None, None, False, []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            lines = [l.strip() for l in (page.extract_text() or '').split('\n') if l.strip()]
            in_table = False
            for li, line in enumerate(lines):
                if meta['gender'] is None:
                    m = re.search(r"(Men|Ladies|Women)[´'’]s?\s*Dual\s*Moguls", line)
                    if m:
                        meta['gender'] = 'M' if m.group(1) == 'Men' else 'W'
                if meta['title'] is None and li == 0:
                    meta['title'] = line
                if meta['date'] is None:
                    m = re.search(r'\b[A-Z]{3}\s*(\d{1,2})\s*([A-Z]{3})\s*(\d{4})\b', line)
                    if m and m.group(2) in MONTHS:
                        meta['date'] = '%s-%02d-%02d' % (m.group(3), MONTHS[m.group(2)], int(m.group(1)))
                m = re.search(r'Number of Competitors:\s*(\d+)', line)
                if m:
                    meta['num_competitors'] = int(m.group(1))
                if re.match(r'^Rank\s*Bib\b', line):
                    in_table = True
                    continue
                if not in_table:
                    continue
                if re.match(r'^\d{1,2} [A-Z]{3} \d{4} /', line) or line.startswith(('www.', 'Report created', 'Jury', 'NOTE', 'LEGEND')):
                    in_table = False
                    continue
                if RE_SKIP.match(line) or 'Report Created' in line:
                    continue
                if RE_STAGE.match(line):
                    stage = stage_name(line)
                    continue
                m = RE_ROW.match(line)
                m2 = None if m else RE_ROW_NOYB.match(line)
                if m2:
                    # 生年の列が無い様式（世界ジュニア 2018 Duved）。途中棄権は行末に DNF（順位は付く）
                    rank, bib, code, name, noc, st2 = m2.groups()
                    cur = {'rank': int(rank), 'bib': int(bib), 'bib_cut': None, 'fis_code': code, 'name': name.strip(),
                           'noc': noc, 'yb': None, 'progression': st2 or '', 'stage': stage, 'page': pno}
                    athletes.append(cur)
                    continue
                if m:
                    st, rank, bib, cut, code, name, noc, yb, prog = m.groups()
                    cur = {'rank': int(rank) if rank and not st else None, 'bib': None if cut else int(bib), 'bib_cut': bib if cut else None,
                           'fis_code': code, 'name': name.strip(), 'noc': noc, 'yb': int(yb),
                           'progression': ((st + ' ') if st else '') + prog.strip(), 'stage': stage, 'page': pno}
                    athletes.append(cur)
                    continue
                if cur is None or line in ('Code Code', 'FIS NSA', 'FIS Ctry', 'Code', 'FIS'):
                    continue
                mt = re.match(r"^((?:(?!Rk\b)[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\.]*\s+)*)(.*)$", line)
                if RE_PROG_TAIL.match(mt.group(2)):
                    # 対戦経過の途中の折り返し。頭に名前の折り返しが付くことがある（「Cooper 16.00, Rk 2」）
                    if mt.group(1).strip():
                        cur['name'] += ' ' + mt.group(1).strip()
                    cur['progression'] = (cur['progression'] + ' ' + mt.group(2)).strip()
                    continue
                if RE_PROG.search(line) or any(w in line.split() for w in STATUS_WORDS):
                    # 対戦経過の折り返し。頭に名前の折り返しが付くことがある（「Oliver」「Christophe」）
                    mp = RE_PROG.search(line)
                    head = line[:mp.start()].strip() if mp else ''
                    if head and re.fullmatch(r"[A-Za-zÀ-ÿ'\-\. ]+", head):
                        cur['name'] += ' ' + head
                        line = line[mp.start():]
                    cur['progression'] = (cur['progression'] + ' ' + line).strip()
                    continue
                if re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\. ]*", line):
                    cur['name'] = (cur['name'] + ' ' + line) if not cur['name'].endswith('-') else cur['name'] + line
                    continue
                problems.append((pno, line))
    return meta, athletes, problems


def load_event(ev, imported_at, log=print):
    ctxs = []
    for pdf in ev['pdfs']:
        path = os.path.join(config.PDF_ROOT, pdf['path'])
        try:
            meta, athletes, problems = parse_pdf(path)
        except Exception as e:  # noqa
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"DM パーサ例外 {pdf['path']}: {e!r}"})
            continue
        g = pdf.get('gender') or meta.get('gender')
        round_id = f"{ev['event_id']}-{g}-F1"
        findings = [Finding('error', round_id, 'layer1', f"読めない行 p{p}: {l}") for p, l in problems]
        if not athletes:
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"{pdf['path']}: DM の最終成績表が読めない"})
            continue
        if meta.get('gender') and pdf.get('gender') and meta['gender'] != pdf['gender']:
            findings.append(Finding('error', round_id, 'layer0', f"見出しの性別 {meta['gender']} が registry の {pdf['gender']} と違う"))
        ranks = [a['rank'] for a in athletes if a['rank'] is not None]
        # 同順位はあり得る（1 回戦で DNF の 2 人など）。昇順であることだけを見る
        if ranks != sorted(ranks):
            bad = next(i for i in range(1, len(ranks)) if ranks[i] < ranks[i - 1])
            findings.append(Finding('error', round_id, 'layer0', f"順位が昇順でない（{bad} 行目付近: {ranks[max(0, bad - 2):bad + 2]}）"))
        # registry の competitor_count_exceptions: 表頭の出場人数の印字が、表の人数・FIS 公式サイトの結果と違うもの（根拠つき）
        for exc in ev.get('competitor_count_exceptions') or []:
            if exc.get('round_id') == round_id and meta.get('num_competitors') == exc.get('printed'):
                meta['num_competitors'] = exc['actual']
                findings.append(Finding('warning', round_id, 'layer0',
                                        f"出場人数の印字 {exc['printed']} を {exc['actual']} として扱う（例外。根拠: {exc['basis']}）"))
        n = meta.get('num_competitors')
        if n and n != len(athletes):
            findings.append(Finding('error', round_id, 'layer0', f"出場人数の印字 {n} と表の人数 {len(athletes)} が違う"))
        cut = [a for a in athletes if a['bib'] is None]
        if cut:
            findings.append(Finding('warning', round_id, 'layer0', f"Bib が「…」で切れて印字された選手 {len(cut)} 名は Bib を空欄にした"
                                    f"（{', '.join(a['name'] + ' ' + a['bib_cut'] + '…' for a in cut[:5])}）"))
        codes = [a['fis_code'] for a in athletes]
        if len(set(codes)) != len(codes):
            findings.append(Finding('error', round_id, 'layer0', 'FIS コードが重複している'))
        cls = {
            'event_id': ev['event_id'], 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
            'discipline': 'DM', 'gender': g, 'round': 'F1', 'round_text': 'Final Result', 'codex': pdf.get('codex'),
            'tier': 'rank', 'panel': None, 'rel': pdf['path'], 'path': path, 'pdf_sha256': pdf.get('sha256'),
            'url': pdf.get('url'), 'page_url': pdf.get('page_url'), 'pages': None, 'name_ja': ev.get('name_ja'),
            'format': ev.get('format'), 'rules_version': None,
        }
        rmeta = {'date': meta.get('date'), 'date_text': meta.get('date'), 'venue': ev.get('place'), 'judges': [],
                 'pace_time': None, 'num_competitors': meta.get('num_competitors'), 'parser_version': PARSER_VERSION,
                 'title': meta.get('title'), 'officials': []}
        records = []
        for a in athletes:
            st = 'OK' if a['rank'] is not None else ('DNS' if 'DNS' in a['progression'] else 'DNF')
            records.append({
                'rank': a['rank'], 'bib': a['bib'], 'saj_no': None, 'fis_code': a['fis_code'], 'athlete_id': a['fis_code'],
                'name': a['name'], 'noc': a['noc'], 'yb': a['yb'], 'affiliation': None, 'club': None,
                'status': st, 'reserve_judge': False, 'counting': True, 'q_block': None, 'best_score': None,
                'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None, 'base_scores': [], 'ded_scores': [],
                'base_total': None, 'ded_total': None, 'turns_total': None, 'run_score': None, 'tie': None, 'page': a['page'],
                'components': {'progression': a['progression'], 'stage': a['stage']},
            })
        ctxs.append({'cls': cls, 'meta': rmeta, 'records': records, 'findings': findings, 'ab_compared': False, 'rules': {}})
        log(f"  {round_id}: {len(records)} 名")
    return ctxs
