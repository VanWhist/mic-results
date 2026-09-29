"""公開データ（data/*.json）の独立検算。ETL のコード（parse_sajmo・verify_nc・scoring・verify）は一切使わない。

    python -m etl.tests.verify_published [--out <json>] [--limit-events N]

A. 印字照合（PDF）: pypdfium2（ETL の pdfplumber とは別エンジン）で文字の座標を取り、自前で行にまとめる。
   選手の行（SAJ は氏名、FIS 様式は FIS コードで見つける）から次の選手の行までの帯にある数値を集め、
   公開データの数値がその帯に印字されているか（多重集合として）を確かめる。列の割り当ては見ない（それは B で見る）。
B. 恒等式（公開データだけで再計算。規則の式を読み直して独自に書いたもの）
   1) スコア = タイム点 + エア + ターン
   2) ターン = 除外後のベース点 + 除外後の減点（5審判は最高・最低を除外、3審判は除外なし）、下限 0.3
      （3審判の大会は審判ごとの下限 0.1 の版もあるので、どちらかに一致すれば可として版を記録）
   3) エア = Σ_ジャンプ 平均_審判 min(10, trunc2(点×DD)) を trunc2
   4) タイム点 = trunc2(clamp(48 − 32 × 秒 ÷ ペースタイム, 0, 20))
   5) 除外印の位置 = 最高・最低の値
   6) 同じラウンド内で、得点の高い方が順位も上（同点は同順位可）
C. moguls-results 由来のラウンド: moguls-results の data/*.json と run 単位で順位・得点・タイム点・エア・ターンを照合
D. 順位のみ（デュアルモーグル）: PDF のページに氏名があり、同じ行に順位の数字があるか
"""
import argparse, collections, glob, json, os, re, sys, unicodedata
from decimal import Decimal, ROUND_DOWN
import ctypes
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

sys.stdout.reconfigure(encoding='utf-8')
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = os.path.dirname(REPO)
PDF_ROOT = os.environ.get('MIC_PDF_ROOT', os.path.join(BASE, 'その他大会のリザルト'))
MOGULS_PDF_ROOT = os.environ.get('MOGULS_PDF_ROOT', os.path.join(BASE, '全試合のリザルト'))
MOGULS_REPO = os.environ.get('MOGULS_RESULTS_REPO', os.path.join(BASE, 'moguls-results'))
Q2 = Decimal('0.01')


def dec(v):
    return None if v is None else Decimal(str(v))


def trunc2(x):
    return x.quantize(Q2, rounding=ROUND_DOWN)


def load_published():
    man = json.load(open(os.path.join(REPO, 'data', 'manifest.json'), encoding='utf-8'))
    f = man['files']
    ev = json.load(open(os.path.join(REPO, 'data', f['events']), encoding='utf-8'))
    ev = ev['events'] if isinstance(ev, dict) else ev
    runs = []
    for fn in f['runs'].values():
        x = json.load(open(os.path.join(REPO, 'data', fn), encoding='utf-8'))
        runs += x['runs'] if isinstance(x, dict) else x
    return man, ev, runs


# ---- A. PDF の行 ---------------------------------------------------------------

_pdf_cache = {}


def page_lines(path, page_no):
    """[(y_center, [(x, text_token)...])] を上から順に。pdfium の文字箱を自前で語・行にまとめる"""
    key = (path, page_no)
    if key in _pdf_cache:
        return _pdf_cache[key]
    doc = pdfium.PdfDocument(path)
    page = doc[page_no - 1]
    tp = page.get_textpage()
    chars = []
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1)
        if not ch or ch in '\r\n':
            continue
        l, b, r, t = tp.get_charbox(i, loose=True)
        if r - l <= 0 and ch.strip() == '':
            continue
        # 行の判定は字形の箱ではなく文字の原点（ベースライン）で行う。小数点だけ低い位置に置く PDF がある
        ox, oy = ctypes.c_double(), ctypes.c_double()
        pdfium_c.FPDFText_GetCharOrigin(tp.raw, i, ctypes.byref(ox), ctypes.byref(oy))
        chars.append((oy.value, l, r, t - b, ch))
    # 行: y の近い文字をまとめる（文字高の 0.45 倍以内）
    chars.sort(key=lambda c: (-c[0], c[1]))
    rows = []
    for c in chars:
        if rows and abs(rows[-1][0] - c[0]) <= max(1.5, 0.45 * max(c[3], 1)):
            rows[-1][1].append(c)
        else:
            rows.append([c[0], [c]])
    out = []
    for y, cs in rows:
        cs.sort(key=lambda c: c[1])
        toks = []
        cur, cx, last_r = '', None, None
        for (_, l, r, h, ch) in cs:
            gap = (l - last_r) if last_r is not None else 0
            if ch.isspace() or (last_r is not None and gap > 0.25 * max(h, 1)):
                if cur.strip():
                    toks.append((cx, cur.strip()))
                cur, cx = '', None
                if ch.isspace():
                    last_r = r
                    continue
            if cx is None:
                cx = l
            cur += ch
            last_r = r
        if cur.strip():
            toks.append((cx, cur.strip()))
        out.append((y, toks))
    out.sort(key=lambda row: -row[0])
    doc.close()
    _pdf_cache[key] = out
    if len(_pdf_cache) > 64:
        _pdf_cache.pop(next(iter(_pdf_cache)))
    return out


def norm(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s or ''))


NUM = re.compile(r'^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$')  # 文字表（NAC 2022）は「-.8」「.128」のように 0 を省く
INT = re.compile(r'^\d{1,3}$')


def num_tokens(rows):
    out = collections.Counter()
    for _, toks in rows:
        for _, t in toks:
            t = unicodedata.normalize('NFKC', t).replace('−', '-')
            # 文字表（NAC 2022）は桁の長い数字が隣とくっつく（「-11.0-33.7」）。数字の直後の「-」で分ける
            for part in re.split(r'[()（）\s]|(?<=\d)(?=-)', t):
                if '..' in part and len(part) % 2 == 0 and part[0::2] == part[1::2]:
                    part = part[0::2]  # 2 回重ね打ちでずれた太字（'1177..2277' = 17.27）
                if re.fullmatch(r'(?:\d{1,2}\.\d){2,}', part):
                    # くっついた審判点（FIS 旧版の様式「2.42.6」= 2.4 と 2.6）
                    for q in re.findall(r'\d{1,2}\.\d', part):
                        out[Decimal(q)] += 1
                    continue
                if NUM.match(part):
                    out[abs(Decimal(part))] += 1
                elif re.search(r'[぀-ヿ㐀-鿿]', part):
                    # 長い所属名が隣の点数欄に重なった語（'兵庫県スキー・スノ1ー4.9'）から数字だけを拾う
                    digits = ''.join(ch for ch in part if ch.isdigit() or ch == '.')
                    if re.fullmatch(r'\d+\.\d+', digits):
                        out[Decimal(digits)] += 1
    return out


def pdf_path(prov):
    p = prov['pdf']
    if p.startswith('moguls-results/'):
        return os.path.join(MOGULS_PDF_ROOT, p[len('moguls-results/'):])
    return os.path.join(PDF_ROOT, p)


def find_anchor(rows, run, fis_style):
    """選手の行の位置（rows の添字）。SAJ は氏名、FIS 様式は FIS コード"""
    want = norm(run['name'])
    cands = []
    for i, (_, toks) in enumerate(rows):
        line = norm(''.join(t for _, t in toks))
        if fis_style:
            if run.get('fis_code') and any(t == str(run['fis_code']) for _, t in toks):
                cands.append(i)
        elif want and want in line:
            bib = str(run['bib']) if run.get('bib') is not None else None
            if bib is None or any(t == bib for _, t in toks):
                cands.append(i)
    if not cands:
        return None
    # 同じ名前がスタート順の一覧などにも載るページがある。結果表の見出し行（'順位 … Total'）より下の行を優先し、
    # その中で数値の一番多い行（結果の行）を選ぶ
    hdr = next((i for i, (_, toks) in enumerate(rows) if toks and toks[0][1].startswith('順位') and any('Total' in t for _, t in toks)), -1)
    return max(cands, key=lambda i: (i > hdr, sum(1 for _, t in rows[i][1] if NUM.match(t)), -i))


def expected_values(run, fis_style, bd_totals=True):
    """公開データのうち印字されているはずの数値（名前, 値）"""
    ev = []
    add = lambda k, v: ev.append((k, abs(dec(v)))) if v is not None else None
    add('bib', run['bib'])
    if run['rank'] is not None:
        add('rank', run['rank'])
    if run['status'] != 'OK' and run['run_score'] is None:
        return ev
    add('seconds', run['seconds'])
    add('time_points', run['time_points'])
    for k, a in enumerate(run['air'] or []):
        for j in ('J6', 'J7'):
            add(f'air{k + 1}.{j}', a.get(j))
        add(f'air{k + 1}.dd', a.get('dd'))
    add('air_total', run['air_total'])
    for i, v in enumerate(run['base'] or []):
        add(f'base.J{i + 1}', v)
    for i, v in enumerate(run['ded'] or []):
        if v is not None and dec(v) != 0:
            add(f'ded.J{i + 1}', v)
    if fis_style and bd_totals:
        # 減点が 0 のとき B の合計を印字しない様式がある（EC 2017 Gaissau の総合）。その値はターン合計と同じなので、そちらで照合する
        if not (dec(run['ded_total']) == 0 and dec(run['base_total']) == dec(run['turns_total'])):
            add('base_total', run['base_total'])
        add('ded_total', run['ded_total'])
    add('turns_total', run['turns_total'])
    add('run_score', run['run_score'])
    return ev


def next_page_carry(path, pg):
    """改ページで次ページ先頭に送られた前ページ最後の選手の 2 行目（表の見出し行の後、次の選手の行の前）の数値。
    表の見出しの無い続きのページ（2017 東海北陸 愛知 男子予選）はページの頭から次の選手の行の前まで"""
    try:
        rows = page_lines(path, pg + 1)
    except Exception:  # noqa  次のページが無い
        return collections.Counter()
    hdr = next((i for i, (_, toks) in enumerate(rows) if toks and toks[0][1].startswith('順位') and any('Total' in t for _, t in toks)), None)
    carry = []
    for _, toks in rows[(hdr + 1) if hdr is not None else 0:]:
        if len(toks) >= 3 and INT.match(toks[0][1]) and INT.match(toks[1][1]):
            break
        carry.append((_, toks))
    return num_tokens(carry[:2])


def _athlete_start(toks):
    return len(toks) >= 3 and INT.match(toks[0][1]) and INT.match(toks[1][1])


def _block_start(toks):
    return any(t in ('B:', 'DNF', 'DNS', 'DSQ') for _, t in toks)


def _split_blocks(rows, first, end):
    """選手の行の範囲 rows[first:end] を走りごとに分ける（各走りは「B:」の行、または途中棄権などの行から始まる）"""
    bs = sorted({first} | {i for i in range(first, end) if _block_start(rows[i][1])})
    return [rows[b:min(nb, b + 4)] for b, nb in zip(bs, bs[1:] + [end])]


def fis_blocks(path, pg, run):
    """総合の報告（1 人の選手の行に、その選手の走りが最後の走りから順に並ぶ）の、その選手の走りごとの行。
    続きの走りが次のページの頭に送られる（NAC 2019 Apex）ので、選手の行が無いページなら前のページから探す。
    戻り値: [走りの行] / 選手の行が見つからなければ None"""
    for page in (pg, pg - 1):
        if page < 1:
            continue
        rows = page_lines(path, page)
        a = find_anchor(rows, run, True)
        if a is None:
            continue
        nxt = [i for i in range(a + 1, len(rows)) if _athlete_start(rows[i][1])]
        blocks = _split_blocks(rows, a, nxt[0] if nxt else len(rows))
        if not nxt:
            try:
                nrows = page_lines(path, page + 1)
            except Exception:  # noqa  次のページが無い
                nrows = []
            first = next((i for i, (_, toks) in enumerate(nrows) if _athlete_start(toks)), len(nrows))
            carry = [i for i in range(first) if _block_start(nrows[i][1])]
            if carry:
                blocks += _split_blocks(nrows, carry[0], first)
        return blocks
    return None


def check_overall_runs(rnd_runs, block_of):
    """総合の報告から組み立てた run（block_of に走りの位置がある）を、その選手の k 本目の走りの行と照合する"""
    issues = []
    for r in rnd_runs:
        path, pg = pdf_path(r['provenance']), r['provenance'].get('page') or 1
        if not os.path.exists(path):
            issues.append((r, 'pdf_missing', path))
            continue
        blocks = fis_blocks(path, pg, r)
        k = block_of[r['run_id']]
        if blocks is None or k >= len(blocks):
            issues.append((r, 'anchor_not_found', f'{os.path.basename(path)} p{pg} 走り {k + 1} 本目'))
            continue
        band = num_tokens(blocks[k])
        for key, v in expected_values(r, True):
            # 2 本目以降の走りの行に順位・ゼッケンは無い（下のラウンドの順位は得点から付けたもの、2 本の良い方の決勝は
            # 1 行目の最終順位を両方の走りに持たせたもの）
            if key in ('bib', 'rank') and k > 0:
                continue
            if band[v] > 0:
                band[v] -= 1
            else:
                issues.append((r, 'value_not_printed', f'{key}={v}（走り {k + 1} 本目）'))
    return issues


def overall_block_index(runs):
    """同じ PDF・同じ FIS コードで複数のラウンドがある run（総合の報告から組み立てたもの）の、選手の行の中での走りの位置。
    総合の報告は上のラウンドの走りから順に並ぶ（決勝 2 → 決勝 1 → 予選）。戻り値: {run_id: 0 始まりの位置}"""
    # 2 本の良い方で順位が付く決勝（q_block R1・R2）は 2 本目（R2）→ 1 本目（R1）の順に載る
    order = ['F3', 'F2', 'R2', 'F1', 'R1', 'Q']
    key = lambda r: r['q_block'] if r.get('q_block') in ('R1', 'R2') else r['round']
    groups = collections.defaultdict(list)
    for r in runs:
        if r['provenance']['parser_version'].startswith('A-') and r.get('q_block') in (None, 'R1', 'R2') \
                and r.get('fis_code') and key(r) in order:
            groups[(r['provenance']['pdf'], r['fis_code'])].append(r)
    out = {}
    for g in groups.values():
        if len(g) > 1 and len({key(r) for r in g}) == len(g):
            for k, r in enumerate(sorted(g, key=lambda r: order.index(key(r)))):
                out[r['run_id']] = k
    return out


def ascii_page_carry(path, pg):
    """文字表（NAC 2022）: ページの最後の選手の 2 本目のジャンプの行が、次のページの下線「=== ===」の次（最初の選手の行の
    前）に送られる。その行の数値"""
    try:
        rows = page_lines(path, pg + 1)
    except Exception:  # noqa  次のページが無い
        return collections.Counter()
    ui = next((i for i, (_, toks) in enumerate(rows) if toks and toks[0][1].startswith('===')), None)
    if ui is None:
        return collections.Counter()
    carry = []
    for row in rows[ui + 1:]:
        if len(row[1]) >= 3 and INT.match(row[1][0][1]) and INT.match(row[1][1][1]):
            break
        carry.append(row)
    return num_tokens(carry)  # 最初の選手の行の前まで（ターン合計の行と 2 本目のジャンプの行の 2 行が送られることもある）


def check_pdf_round(rnd_runs, fis_style, block_of=None, ascii_carry=False, bd_totals=True):
    """ラウンドの run 群を印字と照合。戻り値: [(run, kind, detail)]"""
    issues = []
    block_of = block_of or {}
    multi = [r for r in rnd_runs if r['run_id'] in block_of]
    if multi:
        issues += check_overall_runs(multi, block_of)
        rnd_runs = [r for r in rnd_runs if r['run_id'] not in block_of]
    by_page = collections.defaultdict(list)
    for r in rnd_runs:
        by_page[(pdf_path(r['provenance']), r['provenance'].get('page') or 1)].append(r)
    for (path, pg), runs in by_page.items():
        if not os.path.exists(path):
            issues += [(r, 'pdf_missing', path) for r in runs]
            continue
        try:
            rows = page_lines(path, pg)
        except Exception as e:  # noqa
            issues += [(r, 'pdf_read_error', repr(e)) for r in runs]
            continue
        anchors = {}
        for r in runs:
            a = find_anchor(rows, r, fis_style)
            if a is None:
                issues.append((r, 'anchor_not_found', f'{os.path.basename(path)} p{pg}'))
            else:
                anchors[r['run_id']] = a
        # 帯の終わり: 次の選手の行（公開 run のアンカー、または「数字 数字 …」で始まる行）
        starts = sorted(set(anchors.values()) | {i for i, (_, toks) in enumerate(rows)
                                                 if len(toks) >= 3 and INT.match(toks[0][1]) and INT.match(toks[1][1])})
        for r in runs:
            if r['run_id'] not in anchors:
                continue
            a = anchors[r['run_id']]
            nxt = [s for s in starts if s > a]
            # Q2 の PDF は Q2・Q1 の 2 ブロック（各 3 行）。名前が 3 行に折り返すと 1 行増える（EC 2017 RYKKE ALMENNINGEN）
            span = 7 if r.get('q_block') else 5
            end = min(nxt[0] if nxt else len(rows), a + span)
            band = num_tokens(rows[a:end])
            if not nxt and not fis_style:
                band += next_page_carry(path, pg)
            elif not nxt and ascii_carry:
                band += ascii_page_carry(path, pg)
            for k, v in expected_values(r, fis_style, bd_totals):
                if ascii_carry and k == 'time_points' and v == 0:
                    continue  # 文字表はタイム点 0 を空欄で印字する
                if band[v] > 0:
                    band[v] -= 1
                else:
                    issues.append((r, 'value_not_printed', f'{k}={v}'))
    return issues


def check_rank_only_fis(rnd_runs):
    """FIS 海外大会のデュアルモーグル（fis_dm_pdf、順位のみ）: FIS コードで選手の行を探し、その行に順位・姓・国・生年が
    印字されているかを見る。順位は「2.」のように点が付いたり、切れた Bib とくっついたり（「10.11…」）する。名前は折り返すので姓だけ見る"""
    issues = []
    for r in rnd_runs:
        path, pg = pdf_path(r['provenance']), r['provenance'].get('page') or 1
        if not os.path.exists(path):
            issues.append((r, 'pdf_missing', path))
            continue
        rows = page_lines(path, pg)
        hit = [toks for _, toks in rows if any(t == str(r['fis_code']) or t.endswith(str(r['fis_code'])) or t.startswith(str(r['fis_code']))
                                               for _, t in toks)]
        if not hit:
            issues.append((r, 'code_not_on_page', f"{os.path.basename(path)} p{pg} {r['fis_code']}"))
            continue
        line = ' '.join(t for _, t in hit[0])
        toks = [t for _, t in hit[0]]
        # 順位は行の先頭の語（「2」「2.」「10.11…」）。Bib と同じ数のことがあるので先頭だけを見る
        if r['rank'] is not None and not re.fullmatch(rf"{r['rank']}\.?|{r['rank']}\.\d+…?", toks[0]):
            issues.append((r, 'rank_not_on_line', f"rank={r['rank']} / {line[:60]}"))
        surname = r['name'].split()[0]
        if norm(surname) not in norm(line):
            issues.append((r, 'name_not_on_line', f"{surname} / {line[:60]}"))
        # 国・生年は語として一致するか（名前と国名がくっつく「RiccardoSUI」は語の末尾で見る）
        if r.get('noc') is not None and not any(t == r['noc'] or (t.endswith(r['noc']) and t[:-3].isalpha() and t[-4].islower()) for t in toks):
            issues.append((r, 'noc_not_on_line', f"noc={r['noc']} / {line[:60]}"))
        if r.get('yb') is not None and not any(t == str(r['yb']) for t in toks):
            issues.append((r, 'yb_not_on_line', f"yb={r['yb']} / {line[:60]}"))
    return issues


def check_rank_only(rnd_runs):
    issues = []
    for r in rnd_runs:
        path, pg = pdf_path(r['provenance']), r['provenance'].get('page') or 1
        if not os.path.exists(path):
            issues.append((r, 'pdf_missing', path))
            continue
        rows = page_lines(path, pg)
        want = norm(r['name'])
        hit = [toks for _, toks in rows if want and want in norm(''.join(t for _, t in toks))]
        if not hit and want.isascii():
            # ローマ字の名前に所属の漢字が割り込んだ文字情報（'PARK Sung-Y韓o国un 韓国'、2017 全日本 DM 女子）。
            # ローマ字の名前は行の中の ASCII 以外の文字を無視して探す（名前が違えば見つからないのは同じ）
            hit = [toks for _, toks in rows if want in ''.join(ch for ch in norm(''.join(t for _, t in toks)) if ch.isascii())]
        if not hit:
            issues.append((r, 'name_not_on_page', f'{os.path.basename(path)} p{pg}'))
        elif r['rank'] is not None and not any(any(t == str(r['rank']) for _, t in toks) for toks in hit):
            issues.append((r, 'rank_not_on_line', f"rank={r['rank']}"))
    return issues


# ---- B. 恒等式 ---------------------------------------------------------------

def check_identities(rnd, runs):
    issues = []
    pace = dec(rnd.get('pace_time'))
    for r in runs:
        if r['status'] != 'OK' or r['run_score'] is None:
            continue
        tp, at, tt, sc = (dec(r[k]) for k in ('time_points', 'air_total', 'turns_total', 'run_score'))
        if None not in (tp, at, tt, sc) and tp + at + tt != sc:
            issues.append((r, 'id_score', f'{tp}+{at}+{tt}={tp + at + tt} vs {sc}'))
        if pace and r['seconds'] is not None and tp is not None:
            v = trunc2(min(Decimal(20), max(Decimal(0), Decimal(48) - Decimal(32) * dec(r['seconds']) / pace)))
            if v != tp:
                issues.append((r, 'id_time', f'calc {v} vs {tp}'))
        base, ded = r['base'] or [], r['ded'] or []
        if base and ded and len(base) == len(ded) and None not in base and None not in ded:
            b, d = [dec(x) for x in base], [dec(x) for x in ded]
            n = len(b)
            if n == 5:
                keep_b, keep_d = sorted(b)[1:4], sorted(d)[1:4]
                for arr, disc, name in ((b, r['base_discard'], 'base'), (d, r['ded_discard'], 'ded')):
                    if sorted(disc or []) and len(disc) == 2:
                        vals = sorted(arr[i] for i in disc)
                        if vals != [min(arr), max(arr)]:
                            issues.append((r, 'id_discard', f'{name} 除外 {vals} / 最小最大 {[min(arr), max(arr)]}'))
                    else:
                        issues.append((r, 'id_discard', f'{name} 除外印 {disc}'))
                raw = sum(keep_b) + sum(keep_d)
                cand = [max(raw, Decimal('0.3'))]
            else:
                raw = sum(b) + sum(d)
                cand = [max(raw, Decimal('0.3')), sum(max(x + y, Decimal('0.1')) for x, y in zip(b, d))]
            if tt is not None and tt not in cand:
                issues.append((r, 'id_turns', f'calc {cand} vs {tt}'))
        air = r['air'] or []
        if air and at is not None and all(a.get('dd') is not None and a.get('J6') is not None and a.get('J7') is not None for a in air):
            tot = Decimal(0)
            for a in air:
                vs = [min(Decimal(10), trunc2(dec(a[j]) * dec(a['dd']))) for j in ('J6', 'J7')]
                tot += sum(vs) / len(vs)
            if trunc2(tot) != at:
                issues.append((r, 'id_air', f'calc {trunc2(tot)} vs {at}'))
    # 順位の単調性（同じラウンド・同じ順位ブロック）
    ok = [r for r in runs if r['rank'] is not None and r['run_score'] is not None and r['status'] == 'OK' and not r.get('q_block')]
    groups = collections.defaultdict(list)
    for r in ok:
        groups[r.get('rank_group')].append(r)
    for g in groups.values():
        g.sort(key=lambda r: r['rank'])
        for a, b in zip(g, g[1:]):
            if dec(a['run_score']) < dec(b['run_score']):
                issues.append((b, 'id_rank_order', f"{a['rank']}位 {a['run_score']} < {b['rank']}位 {b['run_score']}"))
            if a['rank'] == b['rank'] and dec(a['run_score']) != dec(b['run_score']) and not (a.get('tie') or b.get('tie')):
                issues.append((b, 'id_rank_tie', f"同順位 {a['rank']} だが得点 {a['run_score']} / {b['run_score']}"))
    return issues


# ---- E. 氏名・所属の崩れ、得点のないラウンド --------------------------------------

JUMP_CHARS = set('0123456789*KSTDLPFGAMNJBHbplogtr')  # ジャンプ記号に使われる文字（KOR・TEAM などの所属は含まない）


def jumpish(t):
    return bool(t) and len(t) <= 6 and set(t) <= JUMP_CHARS and not t.isdigit()


def check_names(rnd, runs):
    issues = []
    for r in runs:
        name = (r.get('name') or '').strip()
        if not name:
            issues.append((r, 'name_blank', ''))
            continue
        toks = name.split()
        cjk = any('぀' <= ch <= '鿿' for ch in name)
        if cjk and (len(toks) > 2 or any(jumpish(t) for t in toks) or re.search(r'\d', name)):
            issues.append((r, 'name_garbled', name))
        aff = (r.get('affiliation') or '').strip()
        if aff and jumpish(aff) and not r.get('noc'):
            issues.append((r, 'affiliation_garbled', aff))
    if rnd['tier'] in ('detail', 'score') and runs and not any(r['run_score'] is not None for r in runs):
        issues.append((runs[0], 'round_no_scores', f'{len(runs)} 名すべて得点なし'))
    return issues


def load_exceptions():
    """印字を正とする登録済みの例外 → {(キー, BIB, 項目): 根拠}。
    全日本は etl/rules/events/規則_YYYY.json（キーは年）、それ以外の大会は registry の recompute_exceptions（キーは event_id）"""
    out = {}
    for fn in glob.glob(os.path.join(REPO, 'etl', 'rules', 'events', '*.json')):
        d = json.load(open(fn, encoding='utf-8'))
        for x in d.get('recompute_exceptions') or []:
            out[(str(d.get('year') or d.get('season')), x.get('bib'), x.get('item'))] = x.get('basis')
    for fn in glob.glob(os.path.join(REPO, 'etl', 'registry', '*.json')):
        for ev in json.load(open(fn, encoding='utf-8')).get('events', []):
            for x in ev.get('recompute_exceptions') or []:
                # SAJ の registry は item（'Time Points' など）、FIS 海外大会（fis_overseas.json）は field（'time_points' など）で書く
                item = x.get('item') or FIELD_ITEM.get(x.get('field'))
                out[(ev['event_id'], x.get('bib'), item)] = x.get('basis')
    return out


EXC_ITEM = {'id_air': 'Air Total', 'id_turns': 'Turns Total', 'id_time': 'Time Points', 'id_score': 'Score'}
FIELD_ITEM = {'air_total': 'Air Total', 'turns_total': 'Turns Total', 'time_points': 'Time Points', 'run_score': 'Score'}


# ---- C. moguls-results との照合 --------------------------------------------------

def load_upstream():
    man = json.load(open(os.path.join(MOGULS_REPO, 'data', 'manifest.json'), encoding='utf-8'))
    files = man['files']['runs']
    files = files.values() if isinstance(files, dict) else files
    out = {}
    for fn in files:
        x = json.load(open(os.path.join(MOGULS_REPO, 'data', fn), encoding='utf-8'))
        for r in (x['runs'] if isinstance(x, dict) else x):
            out[(r['round_id'], r.get('fis_code'), r.get('q_block'))] = r
    return out


def check_upstream(runs, up):
    issues = []
    for r in runs:
        u = up.get((r['round_id'], r.get('fis_code'), r.get('q_block')))
        if u is None:
            issues.append((r, 'up_missing', 'moguls-results に同じ run が無い'))
            continue
        for k in ('rank', 'bib', 'status', 'seconds', 'time_points', 'air_total', 'turns_total', 'run_score', 'best_score'):
            a, b = r.get(k), u.get(k)
            if isinstance(a, (int, float)) or isinstance(b, (int, float)):
                if (a is None) != (b is None) or (a is not None and dec(a) != dec(b)):
                    issues.append((r, 'up_diff', f'{k}: mic {a} / moguls-results {b}'))
            elif a != b:
                issues.append((r, 'up_diff', f'{k}: mic {a} / moguls-results {b}'))
    return issues


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    ap.add_argument('--only', default=None, help='event_id の部分一致で絞る')
    args = ap.parse_args()
    man, events, runs = load_published()
    rounds = {r['round_id']: r for e in events for r in e['rounds']}
    by_round = collections.defaultdict(list)
    for r in runs:
        if args.only and args.only not in r['event_id']:
            continue
        by_round[r['round_id']].append(r)
    up = load_upstream()
    block_of = overall_block_index(runs)
    issues = []
    checked = collections.Counter()
    for i, (rid, rr) in enumerate(sorted(by_round.items())):
        rnd = rounds[rid]
        src = rr[0]['provenance']['parser_version']
        if src.startswith('moguls-results'):
            issues += check_upstream(rr, up)
            issues += check_pdf_round(rr, fis_style=True)
            checked['C+A(fis)'] += len(rr)
        elif src.startswith('LA-'):
            # FIS 海外大会の 1 人 1 行の様式（oneline_a。世界ジュニア 2016）: ターン合計は審判 3 人の和で、ベース合計・減点合計の印字は無い
            issues += check_pdf_round(rr, fis_style=True, bd_totals=False)
            checked['A(fis-oneline)'] += len(rr)
        elif src.startswith('OA-'):
            # FIS 海外大会の旧版の様式（old_a。2014-15・2015-16）: B: / D: の印とベース合計・減点合計の印字が無い
            issues += check_pdf_round(rr, fis_style=True, bd_totals=False)
            checked['A(fis-old)'] += len(rr)
        elif src.startswith('AA-'):
            # FIS 海外大会の文字表（fis_pdf アダプタの ascii_a。NAC 2022）: FIS コードで選手の行を探し、ページ送りの行も見る
            issues += check_pdf_round(rr, fis_style=True, ascii_carry=True)
            checked['A(fis-ascii)'] += len(rr)
        elif src.startswith('A-'):
            # FIS 海外大会（fis_pdf アダプタのパーサ A）: FIS 様式なので FIS コードで選手の行を探す
            issues += check_pdf_round(rr, fis_style=True, block_of=block_of)
            checked['A(fis)'] += len(rr)
        elif src.startswith('FIS-DM'):
            issues += check_rank_only_fis(rr)
            checked['D(fis-dm)'] += len(rr)
        elif rnd['tier'] == 'rank':
            issues += check_rank_only(rr)
            checked['D'] += len(rr)
        else:
            issues += check_pdf_round(rr, fis_style=False)
            checked['A(saj)'] += len(rr)
        issues += check_identities(rnd, rr)
        issues += check_names(rnd, rr)
        if (i + 1) % 100 == 0:
            print(f'  {i + 1}/{len(by_round)} ラウンド', flush=True)
    # 印字を画像で確認済みの例外（規則ファイルに登録済み）は「原本の印字どおり」に分類し直す
    exc = load_exceptions()
    for n, (r, k, d) in enumerate(issues):
        if k in EXC_ITEM:
            key = next((kk for kk in ((r['event_id'], r['bib'], EXC_ITEM[k]), (r['date'][:4], r['bib'], EXC_ITEM[k]))
                        if kk in exc and (kk[0] == r['event_id'] or r['series'] == 'SAJ_AJ')), None)
            if key:
                issues[n] = (r, 'printed_as_is', f'{d}（登録済みの例外: {exc[key][:40]}…）')
    kinds = collections.Counter(k for _, k, _ in issues)
    print('照合した run:', dict(checked), '合計', sum(checked.values()))
    print('不一致:', dict(kinds), '合計', len(issues))
    rows = [{'run_id': r['run_id'], 'round_id': r['round_id'], 'series': r['series'], 'tier': r['tier'], 'round': r['round'],
             'rank': r['rank'], 'name': r['name'], 'kind': k, 'detail': d, 'pdf': r['provenance']['pdf'],
             'page': r['provenance'].get('page')} for r, k, d in issues]
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump({'dataVersion': man['dataVersion'], 'checked': checked, 'kinds': kinds, 'issues': rows}, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
