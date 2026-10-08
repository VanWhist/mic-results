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
try:
    from . import glyph_font as glyph_font_mod
except ImportError:  # スクリプトとして直接使うとき
    import glyph_font as glyph_font_mod

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
SEC_BARE = re.compile(r'^(女子|男子)\s*リザルト(?:\s+(?:成年|少年))?$')  # '男子 リザルト 成年'（2014 宮様。成年・少年は大会ごとに別の codex）
# 「成績表」の見出し（'モーグル男子予選成績表'、2012・2013 東海北陸 愛知県大会）
SEC_SEISEKI = re.compile(r'^モーグル(女子|男子)(予選決勝|予選|決勝)成績表$')  # 予選決勝＝1 本で順位が決まる（2013）
# 英語版の見出し（"Men's Moguls Super Final Result"、2015 全日本・2024 五箇山 AC）。性別・ラウンドを日本語の見出し語に読み替える
SEC_EN = re.compile(r"^(Men's|Ladies'|Women's)\s+Moguls\s+(Super\s*Final|Final|Qualification)\s+Result")
EN_GENDER = {"Men's": '男子', "Ladies'": '女子', "Women's": '女子'}
EN_ROUND = {'Qualification': '予選', 'Final': '決勝', 'SuperFinal': 'スーパーファイナル'}


# 全員の総合順位のページで、下のブロックの始まりを示す印字（'[Results from Final1]'・'[Results from Qualification]'）
BLOCK_MARK = re.compile(r'\[Results from (Final ?1|Qualification)\]')


def block_code(word):
    return 'Q' if word == 'Qualification' else 'F1'


def table_codex(line):
    """表の下に印字される CODEX（その表の CODEX）。CODEX の行でなければ、または番号が読めなければ None。
    'CODEX : 0480 北海道, FIS CODEX : 8321'（2016-17 以降）・'CODEX : 斑尾 5004 (7123.019.91)'・'CODEX:白馬475018( )'
    （旧様式は会場名の後。括弧の中はコースの公認番号）。英語版 'CODEX : 8974 Taira (JPN), SAJ CODEX : 0609' は先頭が FIS の
    CODEX なので SAJ CODEX を取る。区分の総合の部は '0430&0431'、2 つの大会で共通の予選は '0451_0528'（印字のまま）"""
    m = re.search(r'SAJ\s*CODEX\s*[:：]\s*(\d{4}(?:\s*[&_]\s*\d{4})*)', line)
    if m:
        return re.sub(r'\s', '', m.group(1))
    m = re.match(r'CODEX\s*[:：](.*)', line)
    if not m:
        return None
    rest = re.sub(r'\([^)]*\)', ' ', re.split(r'FIS\s*CODEX', m.group(1))[0])
    m = re.search(r'(\d{4,})((?:\s*[&_]\s*\d{4})*)', rest)
    if not m:
        return None
    return m.group(1)[-4:] + re.sub(r'\s', '', m.group(2))  # '白馬475018' は会場名の '白馬47' と 5018


def en_heading(line):
    """英語版の見出し → (性別, ラウンドの見出し語)。英語版でなければ None"""
    m = SEC_EN.match(line)
    if not m:
        return None
    return EN_GENDER[m.group(1)], EN_ROUND[re.sub(r'\s', '', m.group(2))]


def split_en_name(tokens):
    """英語版の氏名と国（'HARADaichi JPN' → ('HARA Daichi', 'JPN')、'FUJIMURA Ikkei JPN' → ('FUJIMURA Ikkei', 'JPN')）。
    姓（大文字）と名（大文字＋小文字）が空白なしで印字された語は境目で分ける（文字は変えない）"""
    noc = tokens[-1] if tokens and re.fullmatch(r'[A-Z]{3}', tokens[-1]) else ''
    words = tokens[:-1] if noc else list(tokens)
    out = []
    for w in words:
        m = re.match(r"^([A-Z][A-Z'-]*?)([A-Z][a-z].*)$", w)
        out += list(m.groups()) if m else [w]
    return ' '.join(out), noc


def untangle_noc(word):
    """国（3 文字の大文字）が氏名に 1 字ずつ重なった語（'MARTINWilliAamUS' = 'MARTIN William' と 'AUS'、
    'JUNJin-WonKOR'、2013 長野県選手権・2013 白馬 47）→ ('MARTIN William', 'AUS')。
    語の末尾が大文字で、最後の 3 つの大文字を除くと「姓（大文字）＋名（大文字＋小文字）」になるときだけ。違えば None"""
    ups = [i for i, ch in enumerate(word) if ch.isupper()]
    if len(ups) < 3 or not word[-1].isupper():
        return None
    drop = set(ups[-3:])
    m = re.fullmatch(r"([A-Z][A-Z'-]*[A-Z])([A-Z][a-z][A-Za-z'-]*)", ''.join(ch for i, ch in enumerate(word) if i not in drop))
    if not m:
        return None
    return f'{m.group(1)} {m.group(2)}', ''.join(word[i] for i in sorted(drop))


def split_ascii_pref(word):
    """ローマ字の氏名の語に所属が空白なしで続く語（'MAR海外'）→ ('MAR', '海外')。所属が一覧に無ければ (word,)"""
    m = re.fullmatch(r"([A-Za-z][A-Za-z'-]*)([^\x00-\x7f]+)", word)
    return m.groups() if m and m.group(2) in PREFS else (word,)


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


# 7 桁の SAJ 番号に続く氏名（ASCII 以外の字で始まる）。先頭に 0 の付いた 8 桁（'05000470鈴木'、2012 東海北陸 愛知）も 7 桁にする
GLUED_SAJNO = re.compile(r'0?(\d{7})([^\x00-\x7f].*)')
# BIB・組・SAJ 番号・氏名が空白なしで続く語（'5男子5000470鈴木'、2012 東海北陸 愛知 男子決勝。表頭 '順位BIB 組 SAJ競技者No'）
GLUED_BIB_GROUP = re.compile(r'(\d{1,3})(?:男子|女子)0?(\d{7})([^\x00-\x7f].*)')
# BIB と SAJ 番号の間の空白が無い語（'1 915000030 吉川 空 …' = BIB 91・SAJ 5000030、2012 東京都・2013 東京都 B級の男子決勝）
GLUED_BIB_SAJNO = re.compile(r'([1-9]\d{0,2})(5\d{6})')


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
    if force and line.startswith('順位') and 'SAJ競技者No' in line:
        # 2012・2013 東海北陸 愛知県大会: '順位 BIB SAJ競技者No 氏名 所属 クラブ名 …'（2013 は J1 … Total が次の行に割れる）
        return True
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
    # 同点欄: 'T1'・'A1'・'S1' など。2013 ふくしま #1 は 'ﾀｰﾝ'（ターン点で分けた）、2012 白馬さのさか 女子は '##'
    if rest and re.fullmatch(r'[A-Z]\d+|ﾀｰﾝ|##', rest[-1]):
        tie = rest[-1]; rest = rest[:-1]
    # 名前・所属・クラブ: 最初の数値の並び（ベース点）の手前まで
    k = 0
    while k < len(rest) and not (is_num(rest[k]) and k + nturn < len(rest) and all(is_num(x) for x in rest[k:k + nturn + 1])):
        k += 1
    head = [t for t in rest[:k] if not is_num(t) and t not in STATUS]  # 完走していない行の '0.00 DNF' は名前・クラブに含めない
    nums_tail = [x for x in rest[k:] if is_num(x)]
    # ローマ字の氏名に所属が空白なしで続く語（'William MAR海外 ｵｰｽﾄﾗﾘｱ'、2012 東京都）は氏名と所属に分ける（氏名は印字のまま）。
    # 氏名の位置（先頭 2 語）だけ。クラブ名（'AP山形'）は分けない
    head = [p for t in head[:2] for p in split_ascii_pref(t)] + head[2:]
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
        if not ok4 and len(rest) >= 2 and rest[-2] == '###':
            # タイム点の欄が '###'（表計算の欄の幅に入らない 10 点以上の値。'… 6.86 20.66 ### 32.81'、2017 白馬さのさか）:
            # タイム点は印字なしとして持ち、エア計・タイム・スコアは印字のまま
            air, tm, sc = (float(x) for x in nums_tail[-3:])
            v4, ok4 = (air, tm, None, sc), True
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


def parse_pdf(path, force=False, glyph_font=None):
    """戻り値: (meta, sections)。sections の要素は parse_sajmo と同じ形（gender / round / code / pages / athletes / nturn）。
    旧様式の表頭が一つも無ければ sections は空。
    glyph_font: 文字が字形の番号のままの PDF（registry の glyph_font。2014 NASPA）。parse_sajmo と同じく文字に戻してから読む"""
    sections = []
    meta = dict(path=path, title=None, venue=None, date=None, codex=None, judges={}, old_layout=False)
    with pdfplumber.open(path) as pdf:
        cur = None
        for pno, page in enumerate(pdf.pages, 1):
            if glyph_font:
                glyph_font_mod.fix_page(page, glyph_font)
                page = glyph_font_mod.drop_stamps(page)
            # 区切りのタブが '(cid:9)' として出て数値にくっつく PDF がある（'(cid:9)(cid:9)29.77'）
            text = page.dedupe_chars().extract_text() or ''
            if re.search(r'^Rk BIB', text, re.M):
                # 英語版は詰めて印字された本物の連続文字（'YOSHII' の I と I）が重ね打ちの許容幅（1pt）でまとまってしまうので
                # 0.5pt で読み直す（saj_dm の英語版と同じ。2015 全日本）
                text = page.dedupe_chars(tolerance=0.5).extract_text() or ''
            lines = [l.strip() for l in re.sub(r'\(cid:\d+\)', ' ', text).split('\n') if l.strip()]
            for li, line in enumerate(lines):
                if li < 12:
                    line = undouble_heading(line)
                # 見出しは空白を除いて照合する（'決 勝リザルト'、2013 埼玉県松之山）
                m = SEC.search(re.sub(r'\s', '', line)) if 'リザルト' in line else SEC_SHORT.match(line)
                mc = SEC_CODEX.match(line)
                mb = SEC_BARE.match(line) if li < 12 else None
                if m is None and li < 12:
                    m = SEC_SEISEKI.match(re.sub(r'\s', '', line))
                me = en_heading(line) if li < 12 else None
                if (m and len(line) < 30 and li < 12) or mc or mb or me:
                    if me:
                        gender, rnd = me
                    else:
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
                        cur = dict(gender=gender, round=rnd, code=None, heading=line, pages=[pno], athletes=[], mixed=(gender == '男女'),
                                   en_heading=bool(me))
                        if mc:
                            cur['codex'] = re.search(r'Codex\s+(\d+)', line).group(1)  # 見出しの CODEX（'男子予選 Codex 0004'）
                        sections.append(cur)
                    continue
                cx = table_codex(line)
                if cx and cur is not None and cur.get('codex') is None:
                    # 表の下の CODEX は、表の始まり以降で最初に出るものがその表のもの（男女の表が同じページに続くと、
                    # ページの最初の CODEX は前の表のもの）
                    cur['codex'] = cx
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
                mg = re.fullmatch(r'【(男子|女子)】', line.strip())
                if mg and cur.get('mixed'):
                    # 男女合同の表の性別の印字（'【女子】' … '【男子】'、2016 札幌 B級）。印字があれば SAJ 番号の照合より優先する
                    if cur['athletes']:
                        cur = dict(gender=None, round=cur['round'], code=None, heading=cur['heading'], pages=[pno], athletes=[],
                                   mixed=True, old=True, nturn=cur.get('nturn', 3), mixed_k=cur.get('mixed_k', 0) + 1)
                        sections.append(cur)
                    cur['gender_mark'] = mg.group(1)
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
                mb = BLOCK_MARK.fullmatch(line.strip())
                if mb:
                    # 全員の総合順位の表（'男子リザルト'、2013 北海道選手権）で、ここから下は予選の得点で並ぶ選手。
                    # スーパーファイナルのページ（2014 ふくしま #1）は '[Results from Final1]' の後が決勝 1 の得点で並ぶ選手
                    cur['from_block'] = block_code(mb.group(1))
                    continue
                toks = [undouble(t) for t in line.split()]
                # SAJ 番号と氏名の間の空白が無い行（'43 91 5001252松本 ベンジャミン …'、2013 埼玉県松之山 B級）
                toks = [p for t in toks for p in (GLUED_SAJNO.fullmatch(t).groups() if GLUED_SAJNO.fullmatch(t) else (t,))]
                if len(toks) > 1 and GLUED_BIB_GROUP.fullmatch(toks[1]):
                    toks = toks[:1] + list(GLUED_BIB_GROUP.fullmatch(toks[1]).groups()) + toks[2:]
                if len(toks) > 2 and toks[0].isdigit() and GLUED_BIB_SAJNO.fullmatch(toks[1]):
                    toks = toks[:1] + list(GLUED_BIB_SAJNO.fullmatch(toks[1]).groups()) + toks[2:]
                # 先頭に 0 の付いた 8 桁の SAJ 番号（'1 2 05000638 寺澤穂乃佳 …'、2015 白馬さのさか 第 2 戦 女子。SAJ 順位表と同じ書き方）は
                # 7 桁に戻す（新しい様式の parse_sajmo と同じ）。順位・BIB の後の 2〜3 語目だけ
                toks = [t[1:] if 1 <= j <= 2 and re.fullmatch(r'0\d{7}', t) else t for j, t in enumerate(toks)]
                if not toks:
                    continue
                if '#N/A' in toks:
                    continue  # 表計算の空の行（'75 0 #N/A #N/A …'、2012 東海北陸 愛知）。選手の行ではない
                if len(toks) > 2 and toks[0].isdigit() and re.fullmatch(r'R\d{1,3}', toks[1]):
                    toks[1] = toks[1][1:]  # 'R' の付いた BIB（'1 R31 5000925 …'、2016 北陸コカ・コーラ杯 女子）
                if any(DOUBLED_NUM.fullmatch(t) for t in line.split()) and len(toks[0]) % 2 == 0 and toks[0].isdigit() \
                        and toks[0][0::2] == toks[0][1::2]:
                    # 太字の行は順位も重ね打ちでずれる（'1155 41 … 1166..7744' = 15 位、2013 東海北陸 愛知）。スコアが重ね打ちの行だけ順位も戻す
                    toks[0] = toks[0][0::2]
                if toks[0] in STATUS or toks[0].isdigit():
                    a = parse_row_old(toks, cur.get('nturn', 3))
                    if a:
                        a['page'] = pno
                        if cur.get('from_block'):
                            a['from_block'] = cur['from_block']
                        if cur.get('fis_header') and re.fullmatch(r'\d{7}', a['sajno'] or ''):
                            a['fisno'], a['sajno'] = a['sajno'], None
                            # 英語版の氏名と国（'HARADaichi JPN'）。以前は 'HARADaichi JPN' が氏名になっていた。
                            # 末尾が国（3 文字の大文字）の行だけ（'Rk' の表頭でも和名・県名の年がある。2012 宮様）
                            parts = ' '.join(x for x in (a['name'], a['pref'], a['club']) if x).split()
                            if parts and re.fullmatch(r'[A-Z]{3}', parts[-1]):
                                a['name'], a['pref'] = split_en_name(parts)
                                a['club'] = ''
                                a['en'] = True
                        if cur.get('mixed') and cur['athletes'] and a.get('rank') == 1 and any(x.get('rank') for x in cur['athletes']):
                            # 男女合同の表: 順位が 1 に戻ったら次の性別（女子→男子）
                            cur = dict(gender=None, round=cur['round'], code=None, heading=cur['heading'], pages=[pno], athletes=[],
                                       mixed=True, old=True, nturn=cur.get('nturn', 3), mixed_k=cur.get('mixed_k', 0) + 1)
                            sections.append(cur)
                        cur['athletes'].append(a)
                        continue
                # 英語版の 2 行目: SAJ 番号 生年 2 本目の技…（'5000947 1997 bL 0.720 8.6 9.0'、2015 全日本）。順位表との照合に SAJ 番号を使う
                if cur.get('fis_header') and cur['athletes'] and cur['athletes'][-1].get('fisno') and 'yb' not in cur['athletes'][-1]:
                    m2 = re.match(r'^(\d{7})\s+((?:19|20)\d\d)\b', line)
                    if m2:
                        cur['athletes'][-1]['sajno'], cur['athletes'][-1]['yb'] = m2.group(1), int(m2.group(2))
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
    # 英語版の見出しの表は、同じ性別の日本語版の表がある PDF（2014-15 の FIS 併催大会。英語版は FIS レースの選手だけで BIB も
    # 違うことがある）では読まない。英語版の見出しを読む前と同じ扱い。英語版だけの PDF（2015 全日本）は英語版を使う
    ja_genders = {s['gender'] for s in ja}
    sections = [s for s in sections if not (s.get('en_heading') and s['gender'] in ja_genders)]
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
        if s.get('gender_mark'):
            s['gender'] = s['gender_mark']
        elif s.get('mixed'):
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
    # 和文の 1 行の表で、外国籍選手の SAJ 番号の欄に FIS コード（2 で始まる 7 桁）が印字され、氏名に国が重なる行
    # （'34 110 2529732 MARTINWilliAamUS オーストラリア …'、2013 長野県選手権・2013 白馬 47）。2 行目に FIS コードが無く、
    # 国の重なりが解けるときだけ、英語版の行と同じく FIS コード・氏名・NOC として持つ（3 列目が FIS コードの印字）
    for s in sections:
        for a in s['athletes']:
            if a.get('fisno') or a.get('en') or not re.fullmatch(r'2\d{6}', a.get('sajno') or '') \
                    or not re.match(r'[A-Za-z]', a.get('name') or ''):
                continue
            words = ' '.join(x for x in (a['name'], a['pref'], a['club']) if x).split()
            un = untangle_noc(''.join(w for w in words if w.isascii()))
            if un:
                a['fisno'], a['sajno'] = a['sajno'], None
                a['name'], a['pref'] = un
                a['club'] = ''
                a['en'] = True
    return meta, sections
