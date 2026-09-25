# -*- coding: utf-8 -*-
"""SAJ国内モーグルリザルト（SAJ03-FM-01/97様式）パーサ。
FIS国際様式用 fis-moguls-pdf-to-excel が対応しない国内リザルト
（札幌スキー連盟公表の全日本・アジアカップ・宮様・北海道・ばんけい等）を
構造化データにする。1選手=2行（1行目: 順位..スコア／2行目: FISNo クラブ 減点 2ndエア）。
"""
import re
import pdfplumber

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
        name = ' '.join(namepart[:-1]) if len(namepart) > 1 else (namepart[0] if namepart else '')
        pref = namepart[-1] if namepart else ''
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
            name = ' '.join(namepart[:-1]) if len(namepart) > 1 else (namepart[0] if namepart else '')
            pref = namepart[-1] if namepart else ''
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
    trail_status = namepart[-1] if namepart and namepart[-1] in STATUS else None
    if trail_status:
        namepart = namepart[:-1]
    name = ' '.join(namepart[:-1]) if len(namepart) > 1 else (namepart[0] if namepart else '')
    pref = namepart[-1] if namepart else ''
    return dict(status=st or trail_status or 'DNF', rank=rank, bib=bib, sajno=sajno,
                name=name, pref=pref, base=None, turns_total=None,
                jump1=None, dd1=None, j6_1=None, j7_1=None,
                air_total=None, time=None, time_point=None, score=None, tie=None,
                raw_partial=partial)

def parse_line2(tokens, nturn=5):
    """FISNO クラブ... D1-Dn Jump2 DD2 Ja Jb  （FISNOなし・2ndエアなしの場合あり）"""
    fisno = None
    i = 0
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


def mixed_order(code):
    """'F-w/m' → ['女子', '男子']。記号が無ければ女子→男子（SAJ 様式の男女合同ページの既定）。"""
    m = re.match(r'^(?:SF|F|Q)-([a-z/]+)$', code or '')
    if m and '/' in m.group(1):
        return [{'w': '女子', 'm': '男子'}.get(x, '?') for x in m.group(1).split('/')]
    return ['女子', '男子']

def parse_pdf(path):
    sections = []
    meta = dict(path=path, title=None, venue=None, date=None, codex=None, judges={})
    with pdfplumber.open(path) as pdf:
        cur = None
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ''
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            code = None
            for li, line in enumerate(lines):
                mc = ROUNDCODE.match(line)
                if mc and li < 4:
                    code = mc.group(1)
                    continue
                m = SEC.search(line)
                if m and 'リザルト' in line and len(line) < 30:
                    gender, rnd = m.group(1) or '', m.group(2)
                    # 同じラウンドが次のページに続くときは同じセクションに足す
                    # （以前はページごとに '男子予選' '男子予選(2)' と分かれていた）。
                    if cur is not None and (cur['gender'], cur['round'], cur['code']) == (gender, rnd, code):
                        cur['pages'].append(pno)
                    else:
                        cur = dict(gender=gender, round=rnd, code=code, heading=line, pages=[pno], athletes=[])
                        sections.append(cur)
                    continue
                mg = GENDER_MARK.match(line)
                if mg and cur is not None:
                    # 男女のラウンドは1ページに【女子】【男子】の2表が並ぶ（全日本2024 SF-w/m）。
                    # 以前は12人が1シートに混ざっていた。表ごとに性別を分けてセクションにする。
                    g = mg.group(1)
                    if cur['gender'] != g:
                        if cur['athletes']:
                            cur = dict(gender=g, round=cur['round'], code=cur['code'], heading=cur['heading'],
                                       pages=[pno], athletes=[])
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
                mm = re.match(r'(J\d)\s*:?\s*\((Turns|Air)\)\s*(.+)', line)
                if mm and mm.group(1) not in meta['judges']:
                    meta['judges'][mm.group(1)] = (mm.group(2), mm.group(3).strip())
                if cur is None:
                    continue
                if line.startswith('順位') and 'Total' in line:
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
                        cur['nturn'] = len(jt)
                    continue
                tokens = line.split()
                if not tokens:
                    continue
                nturn = cur.get('nturn', 5)
                a = None
                # 1行目候補: rank/status + bib + 7桁SAJNo
                if (tokens[0] in STATUS or tokens[0].isdigit()) and len(tokens) >= 3:
                    a = parse_line1(tokens, nturn)
                if a:
                    a['page'] = pno
                    cur['athletes'].append(a)
                    continue
                # 2行目候補: 直前に選手がいて、その選手にまだ line2 が無い
                if cur['athletes'] and 'club' not in cur['athletes'][-1]:
                    last = cur['athletes'][-1]
                    # ヘッダ行等を除外
                    if line.startswith('競技者') or 'CODEX' in line:
                        continue
                    l2 = parse_line2(tokens, nturn)
                    if l2 and (l2.get('ded') or l2.get('fisno') or l2.get('club')):
                        last.update(l2)
    # 同じ表が2回印字されている PDF（結合ミス）: 性別・ラウンド・BIB の集合が同じセクションは後の方を捨てる
    seen, kept = set(), []
    for sec in sections:
        sig = (sec['gender'], sec['round'], sec.get('code'), tuple(sorted((a.get('bib'), a.get('sajno')) for a in sec['athletes'])))
        if sig in seen:
            continue
        seen.add(sig)
        kept.append(sec)
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
