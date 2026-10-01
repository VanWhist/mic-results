"""saj_dm アダプタ: SAJ 様式のデュアルモーグル最終成績（順位のみ、tier rank）。

対応する様式（2020 年代の SAJ07-FM）: 見出し "Men's/Ladies' Dual Moguls Final Result"、表頭
"順位 BIB 競技者NO 氏 名 所属 クラブ名 Progression"、段の見出し（BIG FINAL / SMALL FINAL / Quarter Final /
Eight Final / Round of 32 / Round of 64）、1 選手 = 1〜2 行（対戦経過 "R32-8: B, Tot:19, Rk 1/ ..." が折り返す）。
古い様式（2012 年ごろの男女が横に並ぶ決勝成績表）はまだ読めないので、その大会はエラーとして残す。
順位・BIB・SAJ 番号・氏名・所属・クラブ・対戦経過・最終段だけを持つ。得点や審判点は無い。
"""
import itertools, os, re
import pdfplumber
from ... import config
from ...verify import Finding
from ..saj_aj.parse_sajmo_old import PREFS

SAJNO = re.compile(r'^(?=.*\d)[0-9A-Z]{7}$')
STAGE = re.compile(r'^(BIG FINAL|SMALL FINAL|Quarter Final|Eight Final|Round of \d+|1/\d+ Final)\s*$', re.I)
# 対戦経過の始まり（'R32-8:'、組の無い 1 回戦 'R128, Tot:10'）。長いクラブ名に空白なしで続く PDF がある
# （'…スキー部R64-14:'、'…ｸﾗﾌRﾞ32-3:' は半角カナの濁点が R と数字の間に割り込む）ので、前が英数字でなければ区切る
PROG = re.compile(r'(?<![A-Za-z0-9])(R[ﾞﾟ]?\d+|EF|QF|SF|SmF|F)(?:-\d+:|,\s*Tot:)')
GENDER = {"Men's": 'M', "Ladies'": 'W', "Women's": 'W', "Lady's": 'W'}
# 古い様式（2013-14〜2014-15 の全日本・田沢湖）: 表頭 '順位 BIB SAJNO 氏 名 所属 クラブ名 J1 …'（対戦経過の列が無い）。段の見出し
# （'BIG FINAL'・'BIGFINAL'・'EIGHTS FINAL'）の後に 1 選手 2 行（順位 BIB SAJNO 氏 名 所属 審判点… / クラブ名 2 本目…）。
# 同じ PDF の前のページに DM の前の予選の得点表（表頭 '順位 BIB FISNO …'、段の見出しなし）があるので、段の見出しの後だけ読む
OLD_STAGE = re.compile(r'^(BIG|SMALL|SEMI|QUARTER|EIGHTS?)\s*FINAL\s*$', re.I)
NUMBER = re.compile(r'^-?\d+(\.\d+)?$')
DD = re.compile(r'^\d\.\d{3}$')
# 英語版（2015 全日本）: 表頭 'Rk BIB SAJCode Name Nation Progression'、1 行目 順位 BIB FISコード 姓名 国 対戦経過、
# 2 行目 SAJ 番号（対戦経過の続き）。姓と名が空白なしで印字される（'NISHINobuyuki'）。段の見出しも空白なし（'BIGFINAL'・'Roundof32'）
EN_ROW = re.compile(r'^(?:(\d+)\s+)?(\d+)\s+(\d{7})\s+(\S+)\s+([A-Z]{3})\b\s*(.*)$')
EN_STAGE = {'BIGFINAL': 'BIG FINAL', 'SMALLFINAL': 'SMALL FINAL', 'QuarterFinal': 'Quarter Final', 'EightFinal': 'Eight Final'}
KANA = re.compile(r'[゠-ヿ･-ﾟ]+')
PARSER_VERSION = 'SAJ-DM-1.3'


def _split_ascii(t):
    return ''.join(ch for ch in t if ch.isascii()), ''.join(ch for ch in t if not ch.isascii())


def split_interleaved(rest):
    """氏名以降の語（氏名・所属・クラブ名）。ローマ字の氏名に、隣の欄の文字が 1 字ずつ割り込む文字情報がある。
    - 所属（国名・県名）が割り込み、所属の欄が空になる: 'BAEK-Hyun-韓Mi国n SMX'（2019 田沢湖）、'Mｵeｰlｽaﾄnﾘiｱe'（2020 全日本。
      ｵｰｽﾄﾘｱ）。割り込んだ字が所属の一覧にあるか、カタカナだけのとき、名前から外して所属にする（所属が別に印字されていれば外すだけ。
      'PARK Sung-Y韓o国un 韓国'、2017 全日本）
    - その後ろで名がクラブ名と混ざる: 'SAVEHSHEM東SH京AKI FARﾎｯBﾄOｱﾝDﾄﾞｸﾚｲｼﾞｰ'（2026 B級。SAJ の順位表では
      'SAVEHSHEMSHAKI FARBOD' 東京 ﾎｯﾄｱﾝﾄﾞｸﾚｲｼﾞｰ）。英字を名、残りをクラブ名にする"""
    pref_i = None
    for i, t in enumerate(rest[:2]):
        asc, non = _split_ascii(t)
        if asc and non and re.search(r'[A-Za-z]', asc) and (non in PREFS or KANA.fullmatch(non)):
            rest = rest[:i] + [asc] + ([] if rest[i + 1:i + 2] == [non] else [non]) + rest[i + 1:]
            pref_i = i + 1
            break
    if pref_i is not None and len(rest) > pref_i + 1:
        asc, non = _split_ascii(rest[pref_i + 1])
        if asc and non and re.fullmatch(r'[A-Za-z-]+', asc):
            rest = rest[:pref_i] + [asc, rest[pref_i], non] + rest[pref_i + 2:]
    return rest


def parse_pdf(path):
    meta = dict(title=None, venue=None, date=None, gender=None, judges=[])
    athletes = []
    stage = None
    cur = None
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            # 重ね打ちの太字は同じ位置に同じ文字が重なる。許容幅の既定（1pt）だと、詰めて印字された本物の連続文字
            # （2015 全日本 英語版 'YOSHII' の I と I は 0.96pt 差）まで 1 つにしてしまうので 0.5pt にする
            lines = [l.strip() for l in (page.dedupe_chars(tolerance=0.5).extract_text() or '').split('\n') if l.strip()]
            in_table = False
            for li, line in enumerate(lines):
                if meta['gender'] is None:
                    for k, g in GENDER.items():
                        if line.startswith(k) and 'Dual Moguls' in line:
                            meta['gender'] = g
                if meta['title'] is None and li < 4 and ('大会' in line or '競技' in line):
                    meta['title'] = line
                mm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', line)
                if mm and meta['date'] is None:
                    meta['date'] = '%s-%02d-%02d' % (mm.group(1), int(mm.group(2)), int(mm.group(3)))
                mj = re.match(r'(J\d)\s*\((Turns|Air|Speed|Overall)\)\s*(.+?)\s*\(([^)]*)\)', line)
                if mj and not any(j['judge_no'] == int(mj.group(1)[1:]) for j in meta['judges']):
                    meta['judges'].append({'judge_no': int(mj.group(1)[1:]), 'role': mj.group(2), 'name': mj.group(3).strip(), 'noc': mj.group(4)})
                if line.startswith('順位') and 'Progression' in line:
                    in_table = True
                    continue
                if line.startswith('順位') and 'SAJNO' in line:
                    in_table, old_stage = 'old', False
                    continue
                if line.startswith('Rk BIB SAJCode Name'):
                    in_table = 'en'
                    continue
                md = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', line)
                if md and meta['date'] is None:  # 英語版の日付（日/月/年）
                    meta['date'] = '%s-%02d-%02d' % (md.group(3), int(md.group(2)), int(md.group(1)))
                if not in_table:
                    continue
                if in_table == 'en':
                    if line.startswith(('CODEX', 'HeadJudge', 'Head Judge')):
                        in_table = False
                        continue
                    mr = re.match(r'^Roundof(\d+)$', line)
                    if line in EN_STAGE or mr:
                        stage = EN_STAGE.get(line) or f"Round of {mr.group(1)}"
                        continue
                    me = EN_ROW.match(line)
                    if me:
                        rank, bib, fis, glued, noc, prog = me.groups()
                        ms2 = re.match(r"^([A-Z][A-Z'-]*?)([A-Z][a-z].*)$", glued)  # 姓（大文字）と名（大文字＋小文字）の境目
                        cur = dict(rank=int(rank) if rank else None, bib=int(bib), sajno=None, fisno=fis,
                                   name=' '.join(ms2.groups()) if ms2 else glued, pref='', club='', noc=noc,
                                   progression=prog.strip(), stage=stage, page=pno)
                        athletes.append(cur)
                    elif cur is not None and re.match(r'^\d{7}\b', line) and cur.get('sajno') is None:
                        cur['sajno'], _, more = line.partition(' ')
                        cur['progression'] = (cur['progression'] + ' ' + more).strip()
                    continue
                if in_table == 'old':
                    if line.startswith(('CODEX', 'Head Judge')):
                        in_table = False
                        continue
                    mo = OLD_STAGE.match(line)
                    if mo:
                        stage, old_stage = mo.group(1).upper() + ' FINAL', True
                        continue
                    if not old_stage:
                        continue
                ms = STAGE.match(line)
                if ms:
                    stage = ms.group(1)
                    continue
                toks = line.split()
                # 選手行: 順位 BIB SAJNO 氏 名 所属 クラブ名 対戦経過...
                ranked = len(toks) >= 4 and toks[0].isdigit() and toks[1].isdigit() and SAJNO.match(toks[2])
                # 順位の印字が無い行（BIB SAJNO 氏 名 …）。途中で大会がキャンセルされ、DNF のまま順位が付かなかった選手
                # （2023 田沢湖 女子 QF。順位表では順位が付いている）。読み落とさず、順位なしで持つ
                unranked = (not ranked and len(toks) >= 3 and toks[0].isdigit() and SAJNO.match(toks[1])
                            and PROG.search(line) is not None)
                # SAJ 番号の欄が空の外国籍選手（'4 24 MIN-JI JUN KOR Phoenix park R32-2: …'、2016 北海道選手権 女子。
                # 順位の無い DNF の行 '102 WANG Wenxi中an国g China Jr. Moguls R64-26: B, Tot:0, DNF,'、2020 全日本）。
                # 以前は読み落とし、公開済みの大会からも抜けていた（2020 全日本・2020 猪苗代の中国の選手など）
                no_saj = no_saj_unranked = False
                if in_table is True and not ranked and not unranked and PROG.search(line) is not None and toks[0].isdigit():
                    no_saj = len(toks) >= 4 and toks[1].isdigit() and re.match(r'[A-Za-z]', toks[2]) is not None
                    no_saj_unranked = (not no_saj and len(toks) >= 3 and re.match(r'[A-Za-z]', toks[1]) is not None
                                       and re.search(r'\bDN[FS]\b', line) is not None)
                if ranked or unranked or no_saj or no_saj_unranked:
                    mp = PROG.search(line)
                    head_text, prog = (line[:mp.start()], line[mp.start():].strip()) if mp else (line, '')
                    if in_table == 'old':  # 氏名・所属は審判点の手前まで（クラブ名は次の行）
                        head_text = ' '.join(toks[:3] + list(itertools.takewhile(lambda t: not NUMBER.match(t), toks[3:])))
                    if re.match(r'R[ﾞﾟ]', prog):  # 割り込んだ濁点はクラブ名の最後の字のもの
                        head_text, prog = head_text + prog[1], 'R' + prog[2:]
                    head = head_text.split()
                    if unranked:
                        head = [''] + head
                    if no_saj:
                        head = head[:2] + [''] + head[2:]
                    if no_saj_unranked:
                        head = ['', head[0], ''] + head[1:]
                    rest = split_interleaved(head[3:])
                    # 名と所属の間の空白が無い PDF がある（'キンビッグ 恵茉北海道 TEAM BUMPS'）。名の末尾の県名を所属として切り離す
                    if len(rest) >= 2 and not (len(rest) >= 3 and rest[2] in PREFS):
                        glued = next((p for p in sorted(PREFS, key=len, reverse=True) if rest[1].endswith(p) and len(rest[1]) > len(p)), None)
                        if glued:
                            rest = rest[:1] + [rest[1][:-len(glued)], glued] + rest[2:]
                    # 氏名は「姓 名」の 2 語が基本。所属（県名など）とクラブ名が続く。
                    if len(rest) >= 3 and rest[1] in PREFS and re.search(r'[A-Za-z]', rest[0]):
                        name, pref, club = rest[0], rest[1], ' '.join(rest[2:])  # 1 語のローマ字の名前（'BAEK-Hyun-Min 韓国 SMX'）
                    elif len(rest) >= 3:
                        name, pref, club = ' '.join(rest[:2]), rest[2], ' '.join(rest[3:])
                    elif len(rest) == 2:
                        name, pref, club = rest[0], rest[1], ''
                    else:
                        name, pref, club = ' '.join(rest), '', ''
                    # ローマ字の名前に所属（国名）の漢字が割り込む文字情報がある（'PARK Sung-Y韓o国un 韓国'、2017 全日本 女子）。
                    # ローマ字の名前に限り、所属と同じ漢字を取り除く（和名は所属と同じ字を名前に持つことがあるので触らない）
                    if pref and re.search(r'[A-Za-z]', name) and any(ch in name for ch in pref):
                        name = ''.join(ch for ch in name if ch not in pref or ch.isascii())
                    if ranked:
                        rank, bib, sajno = int(toks[0]), int(toks[1]), toks[2]
                    elif no_saj:
                        rank, bib, sajno = int(toks[0]), int(toks[1]), None
                    elif no_saj_unranked:
                        rank, bib, sajno = None, int(toks[0]), None
                    else:
                        rank, bib, sajno = None, int(toks[0]), toks[1]
                    cur = dict(rank=rank, bib=bib, sajno=sajno, name=name, pref=pref, club=club,
                               progression=prog, stage=stage, page=pno)
                    if (no_saj or no_saj_unranked) and re.fullmatch(r'[A-Z]{3}', pref):
                        cur['noc'] = pref
                    athletes.append(cur)
                    continue
                if in_table == 'old':
                    # 2 行目: クラブ名（続く 2 本目の技コードと DD・審判点の手前まで）
                    if cur is not None and not cur['club'] and not toks[0].isdigit():
                        club = []
                        for i, t in enumerate(toks):
                            if NUMBER.match(t) or (i + 1 < len(toks) and DD.match(toks[i + 1])):
                                break
                            club.append(t)
                        cur['club'] = ' '.join(club)
                    continue
                if cur is not None and PROG.search(line) and not toks[0].isdigit():
                    cur['progression'] = (cur['progression'] + ' ' + line).strip()  # 折り返し行
    return meta, athletes


def athlete_id_of(a):
    if a.get('fisno'):  # 英語版は FIS コードが印字される
        return a['fisno']
    # 外国籍選手の仮の SAJ 番号（'9999999'。2019 田沢湖では韓国の 2 人が同じ番号）は選手を区別しないので、名前で ID を作る
    if a.get('sajno') and not config.is_placeholder_saj(a['sajno']):
        return 'saj-' + str(a['sajno'])
    return 'x-' + config.slug(a['name']) + '-' + config.slug(a.get('pref', ''))


def load_event(ev, imported_at, log=print):
    ctxs = []
    for pdf in ev['pdfs']:
        path = os.path.join(config.PDF_ROOT, pdf['path'])
        try:
            meta, athletes = parse_pdf(path)
        except Exception as e:  # noqa
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"DM パーサ例外 {pdf['path']}: {e!r}"})
            continue
        g = pdf.get('gender') or meta.get('gender')
        findings = []
        round_id = f"{ev['event_id']}-{g}-F1"
        if not athletes:
            ctxs.append({'error_only': True, 'event_id': ev['event_id'],
                         'message': f"{pdf['path']}: DM の最終成績表が読めない（古い様式の可能性。対応待ち）"})
            continue
        if meta.get('gender') and pdf.get('gender') and meta['gender'] != pdf['gender']:
            findings.append(Finding('error', round_id, 'layer0', f"見出しの性別 {meta['gender']} が registry の {pdf['gender']} と違う"))
        ranks = [a['rank'] for a in athletes if a['rank'] is not None]
        # 同順位はあり得る（1 回戦で DNF の 2 人がともに 25 位など。SAJ の順位表も同順位）。昇順であることだけを見る
        if ranks != sorted(ranks):
            bad = next(i for i in range(1, len(ranks)) if ranks[i] < ranks[i - 1])
            findings.append(Finding('error', round_id, 'layer0', f"順位が昇順でない（{bad} 行目付近: {ranks[max(0, bad - 2):bad + 2]}）"))
        cls = {
            'event_id': ev['event_id'], 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
            'discipline': 'DM', 'gender': g, 'round': 'F1', 'round_text': 'Final Result', 'codex': pdf.get('codex'),
            'tier': 'rank', 'panel': None, 'rel': pdf['path'], 'path': path, 'pdf_sha256': pdf.get('sha256'),
            'url': pdf.get('url'), 'page_url': pdf.get('page_url'), 'pages': None, 'name_ja': ev.get('name_ja'),
            'format': ev.get('format'), 'rules_version': None,
        }
        rmeta = {'date': meta.get('date'), 'date_text': meta.get('date'), 'venue': None, 'judges': meta['judges'],
                 'pace_time': None, 'num_competitors': None, 'parser_version': PARSER_VERSION, 'title': meta.get('title'), 'officials': []}
        records = []
        seen = {}
        for a in athletes:
            # 同じ選手の行が順位だけ変えて 2 回印字された PDF がある（2016 北海道選手権 女子の 23 位・24 位 栄 穂乃花。順位表は 23 名）。
            # BIB・SAJ 番号・氏名・対戦経過まで同じ行は印字の重複なので、後の行を読み飛ばす
            sig = (a['bib'], a['sajno'], a['name'], a['progression'])
            if sig in seen:
                findings.append(Finding('warning', round_id, 'layer0',
                                        f"{a['name']}（BIB {a['bib']}）の行が {seen[sig]} 位と {a['rank']} 位に同じ内容で 2 回印字されている。後の行を読み飛ばした"))
                continue
            seen[sig] = a['rank']
            records.append({
                'rank': a['rank'], 'bib': a['bib'], 'saj_no': a['sajno'], 'fis_code': a.get('fisno'), 'athlete_id': athlete_id_of(a),
                'name': a['name'], 'noc': a.get('noc'), 'yb': None, 'affiliation': a['pref'], 'club': a['club'],
                'status': 'OK' if a['rank'] is not None else ('DNS' if 'DNS' in a['progression'] else 'DNF'), 'reserve_judge': False, 'counting': True, 'q_block': None, 'best_score': None,
                'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None, 'base_scores': [], 'ded_scores': [],
                'base_total': None, 'ded_total': None, 'turns_total': None, 'run_score': None, 'tie': None, 'page': a['page'],
                'components': {'progression': a['progression'], 'stage': a['stage']},
            })
        ctxs.append({'cls': cls, 'meta': rmeta, 'records': records, 'findings': findings, 'ab_compared': False, 'rules': {}})
        log(f"  {round_id}: {len(records)} 名")
    return ctxs
