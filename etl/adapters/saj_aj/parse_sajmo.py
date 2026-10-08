# -*- coding: utf-8 -*-
"""SAJ国内モーグルリザルト（SAJ03-FM-01/97様式）パーサ。
FIS国際様式用 fis-moguls-pdf-to-excel が対応しない国内リザルト
（札幌スキー連盟公表の全日本・アジアカップ・宮様・北海道・ばんけい等）を
構造化データにする。1選手=2行（1行目: 順位..スコア／2行目: FISNo クラブ 減点 2ndエア）。
"""
import re
import pdfplumber
try:
    from . import glyph_font as glyph_font_mod
except ImportError:  # スクリプトとして直接使うとき
    import glyph_font as glyph_font_mod

NUM = re.compile(r'^-?\d+(?:\.\d+)?$')
# SAJ番号は通常7桁の数字だが、外国籍選手には '500KOR2' のような英数字が振られる
# （全日本2026）。数字だけに限ると、その選手の行がエラーなしで丸ごと読み飛ばされる。
SAJNO = re.compile(r'^(?=.*\d)[0-9A-Z]{7}$')
STATUS = ('DNF', 'DNS', 'DSQ', 'DQ')

def is_num(t):
    return bool(NUM.match(t))

def parse_line1(tokens, nturn=5):
    """順位 BIB SAJNO 氏名... 所属 J1-Jn Total Jump DD Ja Jb AirTotal Time TimePoint Score [同点]"""
    st = None
    rank = None
    i = 0
    if len(tokens) > 1 and tokens[0].isdigit() and 4 <= len(tokens[0]) <= 6 and SAJNO.match(tokens[1]):
        # 3 桁の BIB は幅が広く、順位の欄とくっついて印字される（'41104' = 41 位 BIB 104。2022 はくのり 男子予選）
        tokens = [tokens[0][:-3], tokens[0][-3:]] + list(tokens[1:])
    if tokens[0] in STATUS:
        st = tokens[0]; i = 1
    elif tokens[0].isdigit() and len(tokens) > 2 and SAJNO.match(tokens[2]):
        rank = int(tokens[0]); i = 1
    elif tokens[0].isdigit() and len(tokens) > 1 and SAJNO.match(tokens[1]):
        # 順位なし（DNF等で順位欄空）だが最初の数字がBIB
        rank = None; i = 0
    elif len(tokens) > 2 and tokens[0].isdigit() and tokens[1].isdigit() and len(tokens[1]) <= 3 \
            and not is_num(tokens[2]):
        # SAJ番号欄が空欄の外国籍選手で順位あり（例: '27 13 MEILINGER Melanie ｵｰｽﾄﾘｱ ...' 全日本2020）。
        # 以前は下の外国籍の分岐が rank を必要とするのに rank をここで立てておらず、行がエラーなしで抜けていた。
        rank = int(tokens[0]); i = 1
    unranked_status = (rank is None and not st and len(tokens) > 1 and tokens[0].isdigit()
                       and len(tokens[0]) <= 3 and not is_num(tokens[1]) and any(t in STATUS for t in tokens))
    if len(tokens) > i + 1 and tokens[i].isdigit() and SAJNO.match(tokens[i+1]):
        bib = int(tokens[i]); sajno = tokens[i+1]
        rest = tokens[i+2:]
    elif (rank is not None or st or unranked_status) and len(tokens) > i + 1 and tokens[i].isdigit() \
            and not is_num(tokens[i+1]):
        # （unranked_status: SAJ番号欄も順位もない DNF 等の外国籍 '108 LIAO Lyuyun 中国 0.00 DNF'。
        #   BIB は3桁までに限る。2行目の7桁 FIS 番号＋クラブ名を選手の1行目と取り違えないため）
        # 外国籍選手など SAJ番号なし（例: '29 15 文 胥瑛 韓国 ...'）
        bib = int(tokens[i]); sajno = None
        rest = tokens[i+1:]
    else:
        return None
    # 末尾から数値をたどる。完走行: ... Total Jump DD J6 J7 AirTotal Time TimePoint Score [tie]
    tie = None
    tail = list(rest)
    if tail and tail[-1] in ('*',) :
        tail = tail[:-1]
    # 同点で順位を決めた選手は同点欄に 'T1' 'T2' と印字される（全日本2025 予選7・8位）。
    # これを数値列の終わりと扱えないと、完走者がエラーなしで DNF になり、名前に所属と技コードが混ざる。
    if tail and re.fullmatch(r'[A-Z]\d+', tail[-1]):  # 同点欄: 全日本は T1/T2、公認大会では A1/A2 の印字もある
        tie = tail[-1]
        tail = tail[:-1]
    # 同点列は稀。末尾スコア直後の追加数値は同点順位とみなす（後続の整合検査で捕捉）
    nums_from_end = []
    j = len(tail) - 1
    while j >= 0 and is_num(tail[j]):
        nums_from_end.append(tail[j]); j -= 1
    nums_from_end.reverse()
    jump1 = None; dd1 = None
    if len(nums_from_end) >= nturn + 9 and (j < 0 or not is_num(tail[j])):
        # ジャンプコード自体が数字（例 "3"）で末尾数値列に含まれた場合
        base = [float(x) for x in nums_from_end[:nturn]]
        turns_total = float(nums_from_end[nturn])
        jump1 = nums_from_end[nturn+1]
        dd1 = float(nums_from_end[nturn+2])
        j6 = float(nums_from_end[nturn+3]); j7 = float(nums_from_end[nturn+4])
        air_total = float(nums_from_end[nturn+5])
        time_s = float(nums_from_end[nturn+6]); time_pt = float(nums_from_end[nturn+7])
        score = float(nums_from_end[nturn+8])
        namepart = tail[:len(tail)-len(nums_from_end)]
        name, pref = _name_pref(namepart, sajno)
        return dict(status=st or 'OK', rank=rank, bib=bib, sajno=sajno,
                    name=name, pref=pref, base=base, turns_total=turns_total,
                    jump1=jump1, dd1=dd1, j6_1=j6, j7_1=j7,
                    air_total=air_total, time=time_s, time_point=time_pt,
                    score=score, tie=tie)
    if j >= 0 and not is_num(tail[j]) and len(nums_from_end) >= 5:
        # tail[j] はジャンプコード（*NJ等含む）
        jump1 = tail[j]
        pre = tail[:j]  # 名前・所属・J1-J5・TurnsTotal
        # pre の末尾から数値: J1-J5 + TurnsTotal = 6個
        pnums = []
        k = len(pre) - 1
        while k >= 0 and is_num(pre[k]):
            pnums.append(pre[k]); k -= 1
        pnums.reverse()
        namepart = pre[:k+1]
        if len(pnums) >= nturn + 1 and len(nums_from_end) >= 7:
            base = [float(x) for x in pnums[-(nturn+1):-1]]
            turns_total = float(pnums[-1])
            dd1 = float(nums_from_end[0])
            j6 = float(nums_from_end[1]); j7 = float(nums_from_end[2])
            air_total = float(nums_from_end[3])
            time_s = float(nums_from_end[4]); time_pt = float(nums_from_end[5])
            score = float(nums_from_end[6])
            if len(nums_from_end) > 7:
                tie = nums_from_end[7]
            name, pref = _name_pref(namepart, sajno)
            return dict(status=st or 'OK', rank=rank, bib=bib, sajno=sajno,
                        name=name, pref=pref, base=base, turns_total=turns_total,
                        jump1=jump1, dd1=dd1, j6_1=j6, j7_1=j7,
                        air_total=air_total, time=time_s, time_point=time_pt,
                        score=score, tie=tie)
    # 完走していない行（DNF: 数値が少ない）
    namepart = []
    partial = []
    for t in rest:
        (partial if is_num(t) else namepart).append(t)
    # 表計算の '#N/A' が印字された行がある（2024-25 B級 3809-0470 予選 BIB 55）。氏名・所属には含めない
    namepart = [t for t in namepart if t != '#N/A']
    trail_status = namepart[-1] if namepart and namepart[-1] in STATUS else None
    if trail_status:
        namepart = namepart[:-1]
    name, pref = _name_pref(namepart, sajno)
    return dict(status=st or trail_status or 'DNF', rank=rank, bib=bib, sajno=sajno,
                name=name, pref=pref, base=None, turns_total=None,
                jump1=None, dd1=None, j6_1=None, j7_1=None,
                air_total=None, time=None, time_point=None, score=None, tie=None,
                raw_partial=partial)


GLUED_NUM = re.compile(r'\d+\.\d')
CJK = re.compile(r'[぀-ヿ㐀-鿿ｦ-ﾟ]')


def split_glued(tokens):
    """所属・クラブ名が長く隣の点数欄に重なると、文字と数字が混ざった 1 語になる
    （'兵庫県スキー・スノ1ー4.9' = '兵庫県スキー・スノー' + '14.9'、'兵庫県スキー・スノー8.0'）。
    漢字・かなを含み、数字と小数点だけを抜き出すと小数になる語は、文字列と数値の 2 語に分ける。"""
    out = []
    for t in tokens:
        # 2 回重ね打ちの太字が少しずれて印字され、文字が 2 つずつ並ぶ数（'1177..2277' = 17.27、2019 B級 1851-0016）
        if '..' in t and len(t) % 2 == 0 and t[0::2] == t[1::2] and re.fullmatch(r'\d+\.\d+', t[0::2]):
            t = t[0::2]
        if CJK.search(t) and re.search(r'\d', t):
            digits = ''.join(ch for ch in t if ch.isdigit() or ch == '.')
            text = ''.join(ch for ch in t if not (ch.isdigit() or ch == '.'))
            if GLUED_NUM.fullmatch(digits) and text:
                out += [text, digits]
                continue
        out.append(t)
    return out


def parse_line2(tokens, nturn=5):
    """FISNO クラブ... D1-Dn Jump2 DD2 Ja Jb  （FISNOなし・2ndエアなしの場合あり）"""
    fisno = None
    i = 0
    tokens = [t for t in tokens if t != '#N/A']  # 表計算の '#N/A' はクラブ名にも値にもしない
    # 県大会を兼ねる大会は、2 行目の先頭に開催県の選手の県内順位 '( 7)' が印字される（2023 松之山。旧様式パーサと同じ扱い）。
    # クラブ名ではないので読み飛ばす（以前は続く FIS 番号を読み落とした）
    m = re.match(r'^\(\s*\d+\)\s*', ' '.join(tokens))
    if m:
        tokens = ' '.join(tokens)[m.end():].split()
    if tokens and re.match(r'^\d{7}$', tokens[0]):
        fisno = tokens[0]; i = 1
    rest = tokens[i:]
    # DD は小数 3 桁（0.710 / 1.050）で印字され、減点（1 桁）と区別できる。DD を錨にして
    # 「クラブ名 [減点×n] 技コード DD Ja Jb」と読む。減点欄の無い様式（B級の一部）でも壊れない。
    dd_idx = [k for k, t in enumerate(rest) if re.fullmatch(r'\d\.\d{3}', t)]
    if len(dd_idx) == 1 and dd_idx[0] >= 1 and len(rest) == dd_idx[0] + 3 and is_num(rest[-1]) and is_num(rest[-2]):
        k = dd_idx[0]
        jump2 = rest[k - 1]
        pre = rest[:k - 1]
        pnums = []
        m = len(pre) - 1
        while m >= 0 and is_num(pre[m]):
            pnums.append(pre[m]); m -= 1
        pnums.reverse()
        ded = [float(x) for x in pnums[-nturn:]] if len(pnums) >= nturn else None
        club = ' '.join(pre[:len(pre) - len(pnums)])
        return dict(fisno=fisno, club=club, ded=ded, jump2=jump2, dd2=float(rest[k]), j6_2=float(rest[k + 1]), j7_2=float(rest[k + 2]))
    if len(rest) >= 4 and not is_num(rest[-4]) and re.fullmatch(r'\d\.\d{1,2}', rest[-3]) and is_num(rest[-2]) and is_num(rest[-1]) \
            and not any(is_num(t) for t in rest[:-4]):
        # 減点の欄が空で、DD・審判の点が末尾のゼロを省いて印字された行（'SON BAY CLUB TT 0.5 4 3.9' = DD 0.500・4.0・3.9、
        # 2019 札幌 FIS 男子決勝）。「クラブ名 技コード DD Ja Jb」で終わるときだけ
        return dict(fisno=fisno, club=' '.join(rest[:-4]), ded=None, jump2=rest[-4], dd2=float(rest[-3]),
                    j6_2=float(rest[-2]), j7_2=float(rest[-1]))
    nums_from_end = []
    j = len(rest) - 1
    while j >= 0 and is_num(rest[j]):
        nums_from_end.append(rest[j]); j -= 1
    nums_from_end.reverse()
    jump2 = None; dd2 = None; j6b = None; j7b = None; ded = None
    if j >= 0 and not is_num(rest[j]) and len(nums_from_end) == 3 and j >= 1:
        # rest[j]=Jump2コード, 直前に減点nturn個があるはず
        cand = rest[:j]
        pnums = []
        k = len(cand) - 1
        while k >= 0 and is_num(cand[k]):
            pnums.append(cand[k]); k -= 1
        pnums.reverse()
        if len(pnums) >= nturn:
            jump2 = rest[j]
            ded = [float(x) for x in pnums[-nturn:]]
            dd2 = float(nums_from_end[0]); j6b = float(nums_from_end[1]); j7b = float(nums_from_end[2])
            club = ' '.join(cand[:k+1][:len(cand[:k+1])-0][:len(cand)-len(pnums)-0][: ] )
            club = ' '.join(cand[:len(cand)-len(pnums)])
            return dict(fisno=fisno, club=club, ded=ded, jump2=jump2, dd2=dd2, j6_2=j6b, j7_2=j7b)
    # ジャンプコードが数字（例 "3"）: D1-Dn code dd ja jb が全部数値で並ぶ
    if len(nums_from_end) == nturn + 4:
        ded = [float(x) for x in nums_from_end[:nturn]]
        jump2 = nums_from_end[nturn]
        dd2 = float(nums_from_end[nturn+1]); j6b = float(nums_from_end[nturn+2]); j7b = float(nums_from_end[nturn+3])
        club = ' '.join(rest[:len(rest)-len(nums_from_end)])
        return dict(fisno=fisno, club=club, ded=ded, jump2=jump2, dd2=dd2, j6_2=j6b, j7_2=j7b)
    # 2ndエアなし: ... D1-Dn のみ / あるいは減点もなし
    if len(nums_from_end) >= nturn:
        ded = [float(x) for x in nums_from_end[-nturn:]]
        club = ' '.join(rest[:len(rest)-len(nums_from_end)])
        return dict(fisno=fisno, club=club, ded=ded, jump2=None, dd2=None, j6_2=None, j7_2=None)
    club = ' '.join(t for t in rest if not is_num(t))
    return dict(fisno=fisno, club=club, ded=None, jump2=None, dd2=None, j6_2=None, j7_2=None)

# 「準決勝」を「決勝」より先に置く。逆だと '男子準決勝リザルト' が性別なしの '決勝' に化ける。
SEC = re.compile(r'(男女|女子|男子)?\s*(?:モーグル|高校生|中学生|小学生|[^\s]{1,4}の部)?\s*(予選[・･]?決勝|予選|準決勝|決勝|スーパーファイナル)\s*リザルト')  # 2012 年ごろは '男子モーグル予選リザルト'  # 予選決勝＝1本で順位が決まる小規模大会
# ページ左上のラウンド記号（SF-m / F-w/m / Q-m 等）。日本語見出しとの対応が年で違うので控えておく
# （2026は SF-m=「決勝」、F-m=「準決勝」）。
ROUNDCODE = re.compile(r'^(?:MO\s+)?((?:SF|F|Q)-[a-z/]+)$')
GENDER_MARK = re.compile(r'^【(男子|女子)】$')
# ラウンドの語が無い見出し（'男子リザルト'、2023 松之山）。round は 'リザルト'（adapter の section_codes が決める）
SEC_BARE = re.compile(r'^(女子|男子)リザルト$')


def _en_heading(line):
    try:
        from . import parse_sajmo_old
    except ImportError:  # スクリプトとして直接使うとき
        import parse_sajmo_old
    return parse_sajmo_old.en_heading(line)


def _block_mark(line):
    """全員の総合順位のページの下のブロックの始まり（'[Results from Final1]' → 'F1'、'[Results from Qualification]' → 'Q'）。
    2018 北海道選手権・2021 札幌 AC のスーパーファイナルのページ。違えば None"""
    try:
        from . import parse_sajmo_old
    except ImportError:  # スクリプトとして直接使うとき
        import parse_sajmo_old
    m = parse_sajmo_old.BLOCK_MARK.fullmatch(line.strip())
    return parse_sajmo_old.block_code(m.group(1)) if m else None


def mixed_order(code):
    """'F-w/m' → ['女子', '男子']。記号が無ければ女子→男子（SAJ 様式の男女合同ページの既定）。"""
    m = re.match(r'^(?:SF|F|Q)-([a-z/]+)$', code or '')
    if m and '/' in m.group(1):
        return [{'w': '女子', 'm': '男子'}.get(x, '?') for x in m.group(1).split('/')]
    return ['女子', '男子']

CATEGORY = re.compile(r'(中学生|高校生|小学生|総合|一般|シニア|マスターズ)の部')


def _interleaved(text):
    """2 つの行の文字が交互に混ざった行があるか（語の大半が 1 文字の、長い行）"""
    for l in text.splitlines():
        toks = l.split()
        if len(toks) >= 20 and sum(1 for t in toks if len(t) == 1) / len(toks) > 0.6:
            return True
    return False


def _name_pref(namepart, sajno):
    """氏名と所属。最後の語が所属（県名・学連・国名）。ただし SAJ 番号の無い選手で 2 語しかなく、最後の語が
    所属の一覧に無いときは所属の印字が無いとみなし、2 語とも氏名にする（'47 120 ノイズ キース …' 2017 東海北陸 愛知）"""
    if not namepart:
        return '', ''
    if len(namepart) == 1:
        return namepart[0], ''
    if sajno is None and len(namepart) == 2:
        try:
            from .parse_sajmo_old import PREFS
        except ImportError:  # スクリプトとして直接使うとき
            from parse_sajmo_old import PREFS
        if namepart[-1] not in PREFS and re.sub(r'[都府県]$', '', namepart[-1]) not in PREFS:
            return ' '.join(namepart), ''
    return ' '.join(namepart[:-1]), namepart[-1]


def _old_row(tokens, nturn):
    try:
        from . import parse_sajmo_old
    except ImportError:  # スクリプトとして直接使うとき
        import parse_sajmo_old
    return parse_sajmo_old.parse_row_old(tokens, nturn)


def _table_codex(line):
    try:
        from . import parse_sajmo_old
    except ImportError:  # スクリプトとして直接使うとき
        import parse_sajmo_old
    return parse_sajmo_old.table_codex(line)


def page_category(top_lines):
    """ページ上部の年齢区分（'中学生の部' など）。見出し語の行（'…リザルト'）は除く（見出し語の中の区分は SEC が扱う）"""
    for l in top_lines:
        if 'リザルト' in l:
            continue
        m = CATEGORY.search(l)
        if m:
            return m.group(0)
    return None


def parse_pdf(path, glyph_font=None):
    """glyph_font: 文字が字形の番号のままの PDF（registry の glyph_font）。glyph_font.fix_page で文字に戻してから読む"""
    sections = []
    meta = dict(path=path, title=None, venue=None, date=None, codex=None, judges={})
    with pdfplumber.open(path) as pdf:
        cur, prev_text = None, None
        doc_nturn = None  # この PDF で表頭から分かったターン審判の人数
        for pno, page in enumerate(pdf.pages, 1):
            glyph_font_mod.fix_page(page, glyph_font)
            page = glyph_font_mod.drop_stamps(page)
            # 太字を同じ文字の重ね打ちで表す PDF がある（'44446666....77772222' = 46.72）。同じ位置の同じ文字は 1 つにする
            page = page.dedupe_chars()
            text = page.extract_text() or ''
            if _interleaved(text):
                # 1 行目と 2 行目の間隔が詰まったページは、行をまとめる縦の許容幅（既定 3）で 2 行が 1 行に混ざる
                # （'9 2 . . 9 1'。2022 はくのり 男子予選）。そのページだけ許容幅を小さくして読み直す
                text = page.extract_text(y_tolerance=2) or ''
            # 同じページが 2 回続けて綴じ込まれた PDF がある（2022 宮様 女子決勝。2 ページ目が 1 ページ目と一字一句同じ）。
            # 読むと同じ選手が 2 回になるので飛ばし、記録に残す
            if text.strip() and text == prev_text:
                meta.setdefault('duplicate_pages', []).append(pno)
                continue
            prev_text = text
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            code = None
            # ページ上部（大会名・審判・コース情報）は選手の行ではない。表の見出し行（'順位 … Total'）より上は読まない。
            # 以前はページ頭の大会名を、前ページ最後の選手の 2 行目（クラブ名）として取り込み、次ページ先頭に続く本物の
            # 2 行目（減点・2 本目のエア）を捨てていた（2024 白馬乗鞍埼玉 A級 予選 34 位など）
            hdr_idx = next((i for i, l in enumerate(lines) if l.startswith(('順位', 'Rk ')) and 'Total' in l), None)
            # 年齢区分（'種目モーグル 中学生の部'）。見出し語の前（ページ上部）に印字される年がある（2024 全日本ジュニア）
            category = page_category(lines[:hdr_idx if hdr_idx is not None else 10])
            for li, line in enumerate(lines):
                mc = ROUNDCODE.match(line)
                if mc and li < 4:
                    code = mc.group(1)
                    continue
                m = SEC.search(line)
                mb = SEC_BARE.match(line)
                me = _en_heading(line)  # 英語版（"Men's Moguls Qualification Result"、2024 五箇山 AC）
                if (m and 'リザルト' in line and len(line) < 30) or mb or me:
                    gender, rnd = (m.group(1) or '', m.group(2)) if m else ((mb.group(1), 'リザルト') if mb else me)
                    # 同じラウンドが次のページに続くときは同じセクションに足す
                    # （以前はページごとに '男子予選' '男子予選(2)' と分かれていた）。
                    if cur is not None and (cur['gender'], cur['round'], cur['code'], cur.get('category')) == (gender, rnd, code, category):
                        cur['pages'].append(pno)
                    else:
                        cur = dict(gender=gender, round=rnd, code=code, heading=line, pages=[pno], athletes=[], category=category)
                        sections.append(cur)
                    continue
                mg = GENDER_MARK.match(line)
                if mg and cur is not None:
                    # 男女のラウンドは1ページに【女子】【男子】の2表が並ぶ（全日本2024 SF-w/m）。
                    # 以前は12人が1シートに混ざっていた。表ごとに性別を分けてセクションにする。
                    g = mg.group(1)
                    if cur['gender'] != g:
                        if cur['athletes']:
                            # 同じページの 2 つ目の表は列の並び（審判の人数）が同じ。見出し行が【男子】より前に印字される
                            # PDF では引き継がないと 5 人審判として読み、氏名に県名が入る（2021 ばんけい A級 第2戦 男子決勝）
                            cur = dict(gender=g, round=cur['round'], code=cur['code'], heading=cur['heading'],
                                       pages=[pno], athletes=[], category=cur.get('category'),
                                       **({'nturn': cur['nturn']} if cur.get('nturn') else {}))
                            sections.append(cur)
                        else:
                            cur['gender'] = g
                    continue
                if meta['title'] is None and ('大会' in line or 'Competition' in line) and li < 6:
                    meta['title'] = line
                if 'スキー場' in line and meta['venue'] is None:
                    meta['venue'] = line
                mm = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', line)
                if mm and meta['date'] is None:
                    meta['date'] = '%s-%02d-%02d' % (mm.group(1), int(mm.group(2)), int(mm.group(3)))
                mm = re.search(r'CODEX\s*[:：]\s*(\d+)', line)
                if mm and meta['codex'] is None:
                    meta['codex'] = mm.group(1)
                cx = _table_codex(line)
                if cx and cur is not None and cur.get('codex') is None:
                    # CODEX は表の下に印字される。表の始まり以降で最初に出る CODEX がその表のもの。男女の表が同じページに
                    # 続く PDF では、ページ（や PDF）の最初の CODEX は前の表（逆の性別）のもの（2022 全日本 男子 0480 が 5480 だった）
                    cur['codex'] = cx
                mm = re.match(r'(J\d)\s*:?\s*\((Turns|Air)\)\s*(.+)', line)
                if mm and mm.group(1) not in meta['judges']:
                    meta['judges'][mm.group(1)] = (mm.group(2), mm.group(3).strip())
                if cur is None or (hdr_idx is not None and li < hdr_idx):
                    continue
                if line.startswith(('順位', 'Rk ')) and 'Total' in line:
                    # 英語版（'Rk BIB SAJNO Name Nation YB …'）: 1 行目の 3 列目は FIS コード、2 行目は SAJ 番号・生年で始まる
                    cur['en'] = line.startswith('Rk ')
                    if cur['gender'] == '男女' or cur.get('mixed'):
                        # 「男女 決勝リザルト」は1セクションに女子表・男子表が並ぶ（順位ヘッダ行が表ごとに出る）。
                        # 表の順序は左上の記号（F-w/m = 女子→男子）で決め、表ごとにセクションを分ける。
                        order = mixed_order(cur.get('code'))
                        k = cur.get('mixed_k', 0)
                        if cur['gender'] == '男女':
                            cur['mixed'] = True
                        else:
                            cur = dict(gender=None, round=cur['round'], code=cur['code'], heading=cur['heading'],
                                       pages=[pno], athletes=[], mixed=True)
                            sections.append(cur)
                        cur['gender'] = order[k] if k < len(order) else '?'
                        cur['mixed_k'] = k + 1
                    jt = re.findall(r'J(\d)', line.split('Total')[0])
                    if jt:
                        cur['nturn'] = doc_nturn = len(jt)
                    elif doc_nturn and 'nturn' not in cur:
                        # 表頭の 'J1 J2 J3' に数字が重なって読めないページ（'順位 BIB FISNO クラブ名 30.82 8.1 7.9 Total …'、
                        # 2019 ハチ北 女子予選）は、同じ PDF の別の表の人数を使う
                        cur['nturn'] = doc_nturn
                    # ターン審判 2 人の様式（'J1 J2 AVE Total'、2024・2025 大阪府ジュニア）: 審判の点の後に 2 人の平均が印字される。
                    # 審判の点ではないので読み飛ばす（印字は ave_base・ave_ded に残す）
                    cur['ave'] = 'AVE' in line.split('Total')[0]
                    # 1 行様式（'… Total 1st 2nd J4 J5 …'）: 技コード 2 つとエア審判の生点が 1 行に並び、DD が印字されない。
                    # 審判点からの再計算はできないので、旧様式と同じ読み方で印字の合計を得点の段階で持つ（adapter が tier=score にする）
                    if '1st' in line and '2nd' in line:
                        cur['oneline'] = True
                    continue
                blk = _block_mark(line)
                if blk:
                    cur['from_block'] = blk
                    continue
                tokens = split_glued(line.split())
                if not tokens:
                    continue
                # 先頭に 0 の付いた 8 桁の SAJ 番号（'05000118'、2018 東京都 予選 17 位。SAJ 順位表と同じ書き方）は 7 桁に戻す。
                # 以前はその行が選手の行と分からず、黙って抜けていた。順位・BIB の後の 3 語目だけ見る
                if len(tokens) > 2 and re.fullmatch(r'0\d{7}', tokens[2]):
                    tokens = tokens[:2] + [tokens[2][1:]] + tokens[3:]
                nturn = cur.get('nturn', 5)
                a = None
                if cur.get('oneline'):
                    if tokens[0] in STATUS or tokens[0].isdigit():
                        a = _old_row(tokens, nturn)
                    if a:
                        a['page'] = pno
                        if cur.get('from_block'):
                            a['from_block'] = cur['from_block']
                        cur['athletes'].append(a)
                    continue
                ave_base = None
                if cur.get('ave') and tokens[0].isdigit():
                    i = next((k for k in range(3, len(tokens)) if re.fullmatch(r'\d+\.\d', tokens[k])), None)
                    if i is not None and i + nturn < len(tokens) and re.fullmatch(r'\d+\.\d', tokens[i + nturn]):
                        ave_base = tokens[i + nturn]
                        tokens = tokens[:i + nturn] + tokens[i + nturn + 1:]
                # 1行目候補: rank/status + bib + 7桁SAJNo
                if (tokens[0] in STATUS or tokens[0].isdigit()) and len(tokens) >= 3:
                    a = parse_line1(tokens, nturn)
                if a:
                    a['page'] = pno
                    if ave_base is not None:
                        a['ave_base'] = float(ave_base)
                    if cur.get('en'):
                        a['en'] = True
                    if cur.get('from_block'):
                        a['from_block'] = cur['from_block']
                    cur['athletes'].append(a)
                    continue
                # 2行目候補: 直前に選手がいて、その選手にまだ line2 が無い
                if cur['athletes'] and 'club' not in cur['athletes'][-1]:
                    last = cur['athletes'][-1]
                    # ヘッダ行等を除外
                    if line.startswith('競技者') or 'CODEX' in line:
                        continue
                    yb = None
                    if last.get('en') and len(tokens) >= 2 and re.fullmatch(r'\d{7}', tokens[0]) and re.fullmatch(r'(?:19|20)\d\d', tokens[1]):
                        yb, tokens = int(tokens[1]), [tokens[0]] + tokens[2:]  # 英語版の 2 行目の生年
                    ave_ded = None
                    if cur.get('ave'):
                        # 減点 J1 J2 AVE の AVE は、2 本目の技コードの直前（DD の 2 つ前）。2 本目が無い行は末尾の数
                        d = next((k for k, t in enumerate(tokens) if re.fullmatch(r'\d\.\d{3}', t)), None)
                        k = d - 2 if d is not None and d >= 2 else len(tokens) - 1
                        if k >= 0 and re.fullmatch(r'\d+\.\d', tokens[k]):
                            ave_ded = tokens[k]
                            tokens = tokens[:k] + tokens[k + 1:]
                    l2 = parse_line2(tokens, nturn)
                    if l2 and (l2.get('ded') or l2.get('fisno') or l2.get('club')):
                        last.update(l2)
                        if yb is not None:
                            last['yb'] = yb
                        if ave_ded is not None:
                            last['ave_ded'] = float(ave_ded)
    # 英語版は 1 行目の 3 列目が FIS コード、2 行目の先頭が SAJ 番号（日本語版と逆）。読んだ位置のまま持っているので入れ替える。
    # 印字の順の照合（adapter の printed_sequence_a）は en の印を見て元の順に並べる
    for sec in sections:
        for a in sec['athletes']:
            if a.get('en'):
                a['sajno'], a['fisno'] = a.get('fisno'), a.get('sajno')
    # 同じ表が2回印字されている PDF（結合ミス）: 性別・ラウンド・BIB の集合が同じセクションは後の方を捨てる
    seen, kept = set(), []
    for sec in sections:
        sig = (sec['gender'], sec['round'], sec.get('code'), tuple(sorted((a.get('bib'), a.get('sajno')) for a in sec['athletes'])))
        if sig in seen:
            continue
        seen.add(sig)
        kept.append(sec)
    # 区分で分けるのは、1 つの PDF に区分が 2 つ以上あるときだけ（区分が 1 つの PDF は従来どおり。ラウンドの識別子を変えない）
    if len({sec.get('category') for sec in kept} - {None}) < 2:
        for sec in kept:
            sec['category'] = None
    return meta, kept

def check(meta, sections):
    """内部整合検査。返り値: (エラーリスト, 検査数)"""
    errs = []
    nchecks = 0
    for s in sections:
        prev = None
        for a in s['athletes']:
            tag = '%s%s %s' % (s['gender'], s['round'], a['name'])
            if a['status'] == 'OK' and a['base'] is not None and a.get('ded') is not None:
                b = sorted(a['base']); d = sorted(a['ded'])
                per = [max(x - y, 0.1) for x, y in zip(a['base'], a['ded'])]
                if len(b) >= 5:
                    cands = [(sum(a['base']) - b[0] - b[-1]) - (sum(a['ded']) - d[0] - d[-1]),
                             sum(per) - max(per) - min(per)]
                else:
                    cands = [sum(per), sum(a['base']) - sum(a['ded'])]
                nchecks += 1
                if not any(abs(c - a['turns_total']) <= 0.051 for c in cands) \
                   and not (abs(a['turns_total'] - 0.3) < 0.001 and min(cands) < 0.3):
                    errs.append('%s: TurnsTotal %.2f != calc %s' % (tag, a['turns_total'], ['%.2f'%c for c in cands]))
            if a['status'] == 'OK' and a['score'] is not None:
                nchecks += 1
                total = (a['turns_total'] or 0) + (a['air_total'] or 0) + (a['time_point'] or 0)
                if abs(total - a['score']) > 0.015:
                    errs.append('%s: Score %.2f != T+A+S %.2f' % (tag, a['score'], total))
            if a['status'] == 'OK' and a['rank'] is not None and prev is not None:
                nchecks += 1
                if a['rank'] < prev and a['rank'] != 1:  # 同点による欠番は正常。逆行のみ異常（1は別グループ再開）
                    errs.append('%s: rank %s follows %s' % (tag, a['rank'], prev))
            if a['rank'] is not None:
                prev = a['rank']
    return errs, nchecks

if __name__ == '__main__':
    import sys, json
    meta, secs = parse_pdf(sys.argv[1])
    errs, n = check(meta, secs)
    print(json.dumps(dict(
        title=meta['title'], date=meta['date'], codex=meta['codex'],
        judges={k: v[1] for k, v in sorted(meta['judges'].items())},
        sections=[dict(gender=s['gender'], round=s['round'], n=len(s['athletes'])) for s in secs],
        check_errors=errs, checks=n), ensure_ascii=False, indent=1))
