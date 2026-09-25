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
SEC = re.compile(r'(男女|女子|男子)?\s*(?:モーグル)?\s*(予選決勝|予選|準決勝|決勝|スーパーファイナル)\s*リザルト')
PREFS = {'北海道', '青森', '岩手', '宮城', '秋田', '山形', '福島', '茨城', '栃木', '群馬', '埼玉', '千葉', '東京', '神奈川', '新潟', '富山', '石川', '福井',
         '山梨', '長野', '岐阜', '静岡', '愛知', '三重', '滋賀', '京都', '大阪', '兵庫', '奈良', '和歌山', '鳥取', '島根', '岡山', '広島', '山口',
         '徳島', '香川', '愛媛', '高知', '福岡', '佐賀', '長崎', '熊本', '大分', '宮崎', '鹿児島', '沖縄', '学連', '韓国', '中国', '台湾', '海外'}


def is_num(t):
    return bool(NUM.match(t))


def is_old_header(line):
    # 表頭が 2 行に割れる年（1 行目 '順位 SAJNO 氏 名 ターン エアー タイム'、2 行目 'SAC BIB FISNO … Pts …'）や
    # 英語版（'Rk BIB FIS Code Name YB … Pts Score Tie'）もある
    return 'Pts' in line and 'Total' in line and (line.startswith(('順位', 'SAC', 'Rk', 'Rank')) or 'BIB' in line)


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
    if not (len(tokens) > i + 1 and tokens[i].isdigit() and SAJNO.match(tokens[i + 1])):
        return None
    bib, sajno = int(tokens[i]), tokens[i + 1]
    rest = tokens[i + 2:]
    tie = None
    if rest and re.fullmatch(r'[A-Z]\d+', rest[-1]):
        tie = rest[-1]; rest = rest[:-1]
    # 名前・所属・クラブ: 最初の数値の並び（ベース点）の手前まで
    k = 0
    while k < len(rest) and not (is_num(rest[k]) and k + nturn < len(rest) and all(is_num(x) for x in rest[k:k + nturn + 1])):
        k += 1
    head = [t for t in rest[:k] if not is_num(t) and t not in STATUS]  # 完走していない行の '0.00 DNF' は名前・クラブに含めない
    nums_tail = [x for x in rest[k:] if is_num(x)]
    pi = next((j for j, t in enumerate(head) if t in PREFS), None)
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
                rec['tie'] = rec['tie'] or nums_tail[-1]
        rec['air_total'], rec['time'], rec['time_point'], rec['score'] = v4
        rec['status'] = st or 'OK'
    else:
        rec['status'] = st or ('DNF' if not any(t in STATUS for t in rest) else next(t for t in rest if t in STATUS))
    return rec


def parse_pdf(path):
    """戻り値: (meta, sections)。sections の要素は parse_sajmo と同じ形（gender / round / code / pages / athletes / nturn）。
    旧様式の表頭が一つも無ければ sections は空。"""
    sections = []
    meta = dict(path=path, title=None, venue=None, date=None, codex=None, judges={}, old_layout=False)
    with pdfplumber.open(path) as pdf:
        cur = None
        for pno, page in enumerate(pdf.pages, 1):
            lines = [l.strip() for l in (page.extract_text() or '').split('\n') if l.strip()]
            for li, line in enumerate(lines):
                m = SEC.search(line)
                if m and 'リザルト' in line and len(line) < 30 and li < 12:
                    gender, rnd = m.group(1) or '', m.group(2)
                    if cur is not None and (cur['gender'], cur['round']) == (gender, rnd) and not cur.get('closed'):
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
                if is_old_header(line):
                    meta['old_layout'] = True
                    jt = re.findall(r'J(\d)', line.split('Total')[0])
                    cur['nturn'] = len(jt) or 3
                    cur['old'] = True
                    cur['fis_header'] = 'FIS Code' in line or line.startswith('Rk')  # 英語版・国際大会版: 3 列目は FIS コード
                    continue
                if not cur.get('old'):
                    continue
                toks = line.split()
                if not toks:
                    continue
                if toks[0] in STATUS or toks[0].isdigit():
                    a = parse_row_old(toks, cur.get('nturn', 3))
                    if a:
                        a['page'] = pno
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
                    fis = toks[0] if re.fullmatch(r'\d{7}', toks[0]) else None
                    body = toks[1:] if fis else toks
                    club_tokens = []
                    for t in body:
                        if is_num(t) or re.fullmatch(r'[A-Za-z0-9*]{1,6}', t):
                            break
                        club_tokens.append(t)
                    last['fisno'] = fis
                    if not last.get('club') and club_tokens:
                        last['club'] = ' '.join(club_tokens)
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
