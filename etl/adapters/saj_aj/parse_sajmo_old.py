# -*- coding: utf-8 -*-
"""SAJ 国内モーグルリザルトの旧様式（2011-12〜2010 年代半ば、SAJ03-FM-01/97 の旧版）を「得点まで」で読む。

旧様式の見分け方: 表頭が "… Total Time Pts スコア 同点"（新様式は "Point"）。
  予選: 順位 BIB SAJNO 氏名 所属 クラブ名 J1 J2 J3 Total 1st 2nd J4 J5 Total Time Pts スコア 同点
        （技コード 2 つ、エア審判 2 名×2 本の生点。DD は印字されない）
  決勝: 順位 BIB FISNO/SAJNO 氏名 所属 J1 J2 J3 Total Jump DD J4 J5 Total Time Pts スコア 同点
        （審判ごとに DD を掛けた列が増える。2 行目に FISNO・クラブ・2 本目）
  男女合同の決勝（「男女 決勝リザルト」）は 1 つの表に女子→男子の順で並び、順位が 1 に戻るところで性別が変わる。
当時の DD 表が手元に無く、時間点の式も現行と違う（18 − 12×time/pace、上限 7.5）ので、審判点からの再計算はせず
印字の合計（ターン計・エア計・タイム・タイム点・スコア）を「得点まで照合済み」の段階で持つ。
"""
import re
import pdfplumber

NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
SAJNO = re.compile(r'^(?=.*\d)[0-9A-Z]{7}$')
STATUS = ('DNF', 'DNS', 'DSQ', 'DQ')
# 'スーパーファイナル決勝リザルト'（2013 ふくしま #2）はスーパーファイナル。'ファイナルリザルト'（2016 全日本）は全員の最終順位を
# 並べ直した表（adapter の drop_relisted が捨てる）。以前はどちらも見出しと読めず、前の表の続きになっていた
SEC = re.compile(r'(男女|女子|男子)?\s*(?:モーグル|高校生|中学生|小学生|[^\s]{1,4}の部)?\s*(予選[・･]?決勝|予選|準決勝|決勝|スーパーファイナル(?:決勝)?|ファイナル)\s*リザルト')
# 「リザルト」の無い見出し（'男子中学予選'、2012 JOC ジュニア）。行全体がこの形のときだけ見出しとみなす
SEC_SHORT = re.compile(r'^(女子|男子)(?:中学|高校|小学)?(予選|決勝)$')
# ページの途中の表の見出し（'女子決勝 Codex 5004'、2013 埼玉県松之山。'決 勝リザルト' のページに女子・男子の表が続く）。
# ページのどこにあっても見出しとみなし、同じ性別・ラウンドの表が続いていればその続き（'男子予選 Codex 0004'）
SEC_CODEX = re.compile(r'^(女子|男子)(予選|決勝)\s+Codex\s+\d+\b')
# ラウンドの語が無い見出し（'男子リザルト'。2013 札幌・2013 北海道選手権・2015 松之山）。round は 'リザルト' とし、
# 1 本勝負か全員の総合順位かは adapter の section_codes が行の中身で決める
SEC_BARE = re.compile(r'^(女子|男子)リザルト$')
PREFS = {'北海道', '青森', '岩手', '宮城', '秋田', '山形', '福島', '茨城', '栃木', '群馬', '埼玉', '千葉', '東京', '神奈川', '新潟', '富山', '石川', '福井',
         '山梨', '長野', '岐阜', '静岡', '愛知', '三重', '滋賀', '京都', '大阪', '兵庫', '奈良', '和歌山', '鳥取', '島根', '岡山', '広島', '山口',
         '徳島', '香川', '愛媛', '高知', '福岡', '佐賀', '長崎', '熊本', '大分', '宮崎', '鹿児島', '沖縄', '学連', '韓国', '中国', '台湾', '海外'}


def is_num(t):
    return bool(NUM.match(t))


# 太字を少しずらした重ね打ちで表す数字（'1199..1166' = 19.16、2013 千葉県松之山の予選のスコア）。ずれが dedupe_chars の
# 許容幅（1pt）より大きく 1 つにまとまらない。本物の数値に '..' は無いので、この形のときだけ 1 字おきに取る
DOUBLED_NUM = re.compile(r'(?:(\d)\1)+\.\.(?:(\d)\2)+')


def undouble(t):
    return t[::2] if DOUBLED_NUM.fullmatch(t) else t


GLUED_SAJNO = re.compile(r'(\d{7})([^\x00-\x7f].*)')  # 7 桁の SAJ 番号に続く氏名（ASCII 以外の字で始まる）


def undouble_heading(line):
    """見出しの重ね打ち（'男男女女 決決勝勝リリザザルルトト'、2014 埼玉県松之山 B級）。空白を除いた全部の字が 2 つずつ
    並ぶときだけ 1 字おきに取る"""
    s = re.sub(r'\s', '', line)
    if len(s) >= 8 and len(s) % 2 == 0 and s[0::2] == s[1::2]:
        return s[0::2]
    return line


def is_old_header(line, force=False):
    # 表頭が 2 行に割れる年（1 行目 '順位 SAJNO 氏 名 ターン エアー タイム'、2 行目 'SAC BIB FISNO … Pts …'）や
    # 英語版（'Rk BIB FIS Code Name YB … Pts Score Tie'）もある。force のときは 'Point' 表頭（2013〜2016 年の過渡期の様式）も
    # 同じ「末尾 4 列＝エア計・タイム・タイム点・スコア」として読む
    if force and line.startswith('順位') and 'SAS' in line and 'Score' in line:
        # 表頭が 3 行に割れる年（2013 埼玉県松之山: 'SAJ競 Turns …' / '順位 SAS BIB 氏名 所属 クラブ名 Score Tie' /
        # '技者№ J1 J2 J3 Turn 1st 2nd … Total Time Time'）。1 選手 1 行で、末尾 4 列は同じ並び
        return True
    if not ('Total' in line and (line.startswith(('順位', 'SAC', 'Rk', 'Rank')) or 'BIB' in line)):
        return False
    return 'Pts' in line or (force and ('Point' in line or 'Score' in line or 'スコア' in line))


def parse_row_old(tokens, nturn):
    """旧様式の 1 行目 → 得点まで。完走行は末尾 4 つが エア計・タイム・タイム点・スコア。"""
    st = None
    i = 0
    rank = None
    if tokens[0] in STATUS:
        st = tokens[0]; i = 1
    elif tokens[0].isdigit() and len(tokens) > 3 and tokens[1].isdigit() and tokens[2].isdigit() and SAJNO.match(tokens[3]):
        rank = int(tokens[0]); i = 2  # 順位 SAC BIB SAJNO（2012 松之山: SAC 列がある年）
    elif tokens[0].isdigit() and len(tokens) > 2 and SAJNO.match(tokens[2]):
        rank = int(tokens[0]); i = 1
    elif tokens[0].isdigit() and len(tokens) > 1 and SAJNO.match(tokens[1]):
        i = 0
    elif tokens[0].isdigit() and len(tokens) > 3 and tokens[1].isdigit() and len(tokens[1]) <= 3 and not is_num(tokens[2])             and any(is_num(t) for t in tokens[3:]):
        # SAJ 番号の無い外国籍選手（'41 55 William MAR海外 ｵｰｽﾄﾗﾘｱ …'）: 順位 BIB 氏名 …
        rank = int(tokens[0]); i = 1
        bib, sajno = int(tokens[1]), None
        rest = tokens[2:]
        return _finish_row(rank, st, bib, sajno, rest, nturn)
    if not (len(tokens) > i + 1 and tokens[i].isdigit() and SAJNO.match(tokens[i + 1])):
        return None
    bib, sajno = int(tokens[i]), tokens[i + 1]
    rest = tokens[i + 2:]
    return _finish_row(rank, st, bib, sajno, rest, nturn)


def _finish_row(rank, st, bib, sajno, rest, nturn):
    tie = None
    if rest and re.fullmatch(r'[A-Z]\d+', rest[-1]):
        tie = rest[-1]; rest = rest[:-1]
    # 名前・所属・クラブ: 最初の数値の並び（ベース点）の手前まで
    k = 0
    while k < len(rest) and not (is_num(rest[k]) and k + nturn < len(rest) and all(is_num(x) for x in rest[k:k + nturn + 1])):
        k += 1
    head = [t for t in rest[:k] if not is_num(t) and t not in STATUS]  # 完走していない行の '0.00 DNF' は名前・クラブに含めない
    nums_tail = [x for x in rest[k:] if is_num(x)]
    # 所属は 2 語目以降から探す（姓が県名と同じ選手がいる: '山口 卓也 長野県'）。'長野県' '大阪府' のような表記も県名とみなす
    pi = next((j for j in range(1, len(head)) if head[j] in PREFS or re.sub(r'[都府県]$', '', head[j]) in PREFS), None)
    if pi is None:
        name, pref, club = ' '.join(head[:2]) if len(head) >= 2 else ' '.join(head), (head[2] if len(head) > 2 else ''), ' '.join(head[3:])
        # 所属が県名一覧に無いとき: 「姓 名 所属 クラブ…」と仮定
    else:
        name, pref, club = ' '.join(head[:pi]), head[pi], ' '.join(head[pi + 1:])
    rec = dict(status=st or 'OK', rank=rank, bib=bib, sajno=sajno, name=name, pref=pref, club=club, tie=tie,
               base=None, turns_total=None, air_total=None, time=None, time_point=None, score=None, raw_partial=nums_tail)
    if k < len(rest) and len(nums_tail) >= nturn + 1 + 4:
        rec['base'] = [float(x) for x in rest[k:k + nturn]]
        rec['turns_total'] = float(rest[k + nturn])
        # 末尾は エア計 タイム タイム点 スコア [同点値]。同点欄は数値（1.5 など）で印字されることがあるので、
        # 「スコア＝ターン計＋エア計＋タイム点」が成り立つ方を採る
        def pick(vals):
            air, tm, pts, sc = (float(x) for x in vals)
            return abs(rec['turns_total'] + air + pts - sc) <= 0.02, (air, tm, pts, sc)
        ok4, v4 = pick(nums_tail[-4:])
        if not ok4 and len(nums_tail) >= nturn + 1 + 5:
            ok5, v5 = pick(nums_tail[-5:-1])
            if ok5:
                v4 = v5
                ok4 = True
                rec['tie'] = rec['tie'] or nums_tail[-1]
        if not ok4:
            # タイムの欄が空でタイム点 0.00 の行（'… 1.02 0.00 1.32'、2013 埼玉県松之山 B級 予選の 65・66 位）。
            # 末尾 3 つ（エア計・タイム点・スコア）で式が合い、タイム点が 0 のときだけ、タイムなしとして読む
            air, pts, sc = (float(x) for x in nums_tail[-3:])
            if pts == 0 and abs(rec['turns_total'] + air - sc) <= 0.02:
                v4 = (air, None, pts, sc)
        rec['air_total'], rec['time'], rec['time_point'], rec['score'] = v4
        rec['status'] = st or 'OK'
    else:
        rec['status'] = st or ('DNF' if not any(t in STATUS for t in rest) else next(t for t in rest if t in STATUS))
    return rec


def parse_pdf(path, force=False):
    """戻り値: (meta, sections)。sections の要素は parse_sajmo と同じ形（gender / round / code / pages / athletes / nturn）。
    旧様式の表頭が一つも無ければ sections は空。"""
    sections = []
    meta = dict(path=path, title=None, venue=None, date=None, codex=None, judges={}, old_layout=False)
    with pdfplumber.open(path) as pdf:
        cur = None
        for pno, page in enumerate(pdf.pages, 1):
            # 区切りのタブが '(cid:9)' として出て数値にくっつく PDF がある（'(cid:9)(cid:9)29.77'）
            lines = [l.strip() for l in re.sub(r'\(cid:\d+\)', ' ', page.dedupe_chars().extract_text() or '').split('\n') if l.strip()]
            for li, line in enumerate(lines):
                if li < 12:
                    line = undouble_heading(line)
                # 見出しは空白を除いて照合する（'決 勝リザルト'、2013 埼玉県松之山）
                m = SEC.search(re.sub(r'\s', '', line)) if 'リザルト' in line else SEC_SHORT.match(line)
                mc = SEC_CODEX.match(line)
                mb = SEC_BARE.match(line) if li < 12 else None
                if (m and len(line) < 30 and li < 12) or mc or mb:
                    m = mc or m or mb
                    gender, rnd = m.group(1) or '', (m.group(2) if m is not mb else 'リザルト')
                    if rnd == 'スーパーファイナル決勝':
                        rnd = 'スーパーファイナル'
                    # 見出しの文言まで同じときだけ前の表の続き（'男子成年の部決勝リザルト' の後の '男子決勝リザルト' は別の表。2013 宮様）
                    if (cur is not None and (cur['gender'], cur['round']) == (gender, rnd) and not cur.get('closed')
                            and (mc or re.sub(r'\s', '', cur['heading']) == re.sub(r'\s', '', line))):
                        if pno not in cur['pages']:
                            cur['pages'].append(pno)
                    else:
                        cur = dict(gender=gender, round=rnd, code=None, heading=line, pages=[pno], athletes=[], mixed=(gender == '男女'))
                        sections.append(cur)
                    continue
                if meta['title'] is None and li < 6 and ('大会' in line or '競技会' in line):
                    meta['title'] = line
                if ('スキー場' in line or 'リゾート' in line) and meta['venue'] is None and li < 8:
                    meta['venue'] = line
                mm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', line)
                if mm and meta['date'] is None:
                    meta['date'] = '%s-%02d-%02d' % (mm.group(1), int(mm.group(2)), int(mm.group(3)))
                mj = re.match(r'(J\d)\s*:?\s*\((ターン|エアー|エア|Turns|Air)\)\s*(.+)', line)
                if mj and mj.group(1) not in meta['judges']:
                    role = 'Turns' if mj.group(2) in ('ターン', 'Turns') else 'Air'
                    meta['judges'][mj.group(1)] = (role, mj.group(3).strip())
                if cur is None:
                    continue
                if is_old_header(line, force):
                    meta['old_layout'] = True
                    jt = re.findall(r'J(\d)', line.split('Total')[0])
                    cur['nturn'] = len(jt) or 3
                    cur['old'] = True
                    cur['fis_header'] = 'FIS Code' in line or line.startswith('Rk')  # 英語版・国際大会版: 3 列目は FIS コード
                    continue
                if not cur.get('old'):
                    continue
                if line.strip() == '[Results from Qualification]' and cur['round'] == 'リザルト':
                    # 全員の総合順位の表（'男子リザルト'、2013 北海道選手権）で、ここから下は予選の得点で並ぶ選手
                    cur['from_q'] = True
                    continue
                toks = [undouble(t) for t in line.split()]
                # SAJ 番号と氏名の間の空白が無い行（'43 91 5001252松本 ベンジャミン …'、2013 埼玉県松之山 B級）
                toks = [p for t in toks for p in (GLUED_SAJNO.fullmatch(t).groups() if GLUED_SAJNO.fullmatch(t) else (t,))]
                if not toks:
                    continue
                if toks[0] in STATUS or toks[0].isdigit():
                    a = parse_row_old(toks, cur.get('nturn', 3))
                    if a:
                        a['page'] = pno
                        if cur.get('from_q'):
                            a['from_q'] = True
                        if cur.get('fis_header') and re.fullmatch(r'\d{7}', a['sajno'] or ''):
                            a['fisno'], a['sajno'] = a['sajno'], None
                        if cur.get('mixed') and cur['athletes'] and a.get('rank') == 1 and any(x.get('rank') for x in cur['athletes']):
                            # 男女合同の表: 順位が 1 に戻ったら次の性別（女子→男子）
                            cur = dict(gender=None, round=cur['round'], code=None, heading=cur['heading'], pages=[pno], athletes=[],
                                       mixed=True, old=True, nturn=cur.get('nturn', 3), mixed_k=cur.get('mixed_k', 0) + 1)
                            sections.append(cur)
                        cur['athletes'].append(a)
                        continue
                # 2 行目: FISNO クラブ … （score 段階ではクラブと FISNO だけ使う）
                if cur['athletes'] and 'fisno' not in cur['athletes'][-1]:
                    last = cur['athletes'][-1]
                    # 県大会を兼ねる決勝は、2 行目の先頭に開催県の選手の県内順位 '( 1)' が印字される（表頭 2 行目 '埼玉 BIB FISNO …'）。
                    # クラブ名ではないので読み飛ばす（以前はクラブ名になり、続く FISNO とクラブ名を読み落としていた）
                    toks = re.sub(r'^\(\s*\d+\)\s*', '', line).split()
                    fis = toks[0] if toks and re.fullmatch(r'\d{7}', toks[0]) else None
                    body = toks[1:] if fis else toks
                    club_tokens = []
                    for t in body:
                        if is_num(t) or re.fullmatch(r'[A-Za-z0-9*]{1,6}', t):
                            break
                        club_tokens.append(t)
                    last['fisno'] = fis
                    if not last.get('club') and club_tokens:
                        last['club'] = ' '.join(club_tokens)
    # 表頭も選手の行も無い見出し（'決 勝リザルト' の直後に 'Codex' 付きの表の見出しが続くページ）は表ではないので除く。
    # 表頭があって行を読めなかった表は残す（「選手が1人も読めていない」のエラーにする。2016 B級 1960 の女子）
    sections = [s for s in sections if s['athletes'] or s.get('old')]
    # 英語版（FIS コード）と日本語版（SAJ 番号）が同じ PDF に重複しているとき: 日本語版を残し、FIS コードだけ写す
    ja = [s for s in sections if not s.get('fis_header')]
    for s in [s for s in sections if s.get('fis_header')]:
        twin = next((t for t in ja if (t['gender'], t['round']) == (s['gender'], s['round'])
                     and {a['bib'] for a in t['athletes']} == {a['bib'] for a in s['athletes']}), None)
        if twin is not None:
            by_bib = {a['bib']: a for a in s['athletes']}
            for a in twin['athletes']:
                if not a.get('fisno') and by_bib.get(a['bib'], {}).get('fisno'):
                    a['fisno'] = by_bib[a['bib']]['fisno']
            sections.remove(s)
    # 男女合同の表: 性別を予選の表（性別つき）の SAJ 番号から決める。決められなければ順序（女子→男子）
    by_no = {}
    for s in sections:
        if s['gender'] in ('男子', '女子'):
            for a in s['athletes']:
                by_no[a['sajno']] = s['gender']
    order = ['女子', '男子']
    for s in sections:
        if s['gender'] == '' and by_no:
            # 性別の無い見出し（'決勝リザルト' だけ）: 予選の表の SAJ 番号から性別を決める
            votes = {}
            for a in s['athletes']:
                g = by_no.get(a['sajno'])
                if g:
                    votes[g] = votes.get(g, 0) + 1
            if votes:
                s['gender'] = max(votes, key=votes.get)
        if s.get('mixed'):
            votes = {}
            for a in s['athletes']:
                g = by_no.get(a['sajno'])
                if g:
                    votes[g] = votes.get(g, 0) + 1
            if votes:
                s['gender'] = max(votes, key=votes.get)
            else:
                k = s.get('mixed_k', 0)
                s['gender'] = order[k] if k < len(order) else '?'
    return meta, sections
