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


NUM = re.compile(r'^[+-]?\d+(?:\.\d+)?$')
INT = re.compile(r'^\d{1,3}$')


def num_tokens(rows):
    out = collections.Counter()
    for _, toks in rows:
        for _, t in toks:
            t = unicodedata.normalize('NFKC', t).replace('−', '-')
            for part in re.split(r'[()（）\s]', t):
                if NUM.match(part):
                    out[abs(Decimal(part))] += 1
    return out


def pdf_path(prov):
    p = prov['pdf']
    if p.startswith('moguls-results/'):
        return os.path.join(MOGULS_PDF_ROOT, p[len('moguls-results/'):])
    return os.path.join(PDF_ROOT, p)


def find_anchor(rows, run, fis_style):
    """選手の行の位置（rows の添字）。SAJ は氏名、FIS 様式は FIS コード"""
    want = norm(run['name'])
    for i, (_, toks) in enumerate(rows):
        line = norm(''.join(t for _, t in toks))
        if fis_style:
            if run.get('fis_code') and any(t == str(run['fis_code']) for _, t in toks):
                return i
        elif want and want in line:
            bib = str(run['bib']) if run.get('bib') is not None else None
            if bib is None or any(t == bib for _, t in toks):
                return i
    return None


def expected_values(run, fis_style):
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
    if fis_style:
        add('base_total', run['base_total'])
        add('ded_total', run['ded_total'])
    add('turns_total', run['turns_total'])
    add('run_score', run['run_score'])
    return ev


def check_pdf_round(rnd_runs, fis_style):
    """ラウンドの run 群を印字と照合。戻り値: [(run, kind, detail)]"""
    issues = []
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
            span = 7 if r.get('q_block') else 4  # Q2 の PDF は Q2・Q1 の 2 ブロック（各 3 行）
            end = min(nxt[0] if nxt else len(rows), a + span)
            band = num_tokens(rows[a:end])
            for k, v in expected_values(r, fis_style):
                if band[v] > 0:
                    band[v] -= 1
                else:
                    issues.append((r, 'value_not_printed', f'{k}={v}'))
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
    """etl/rules/events/*.json の recompute_exceptions（印字を画像で確認済みの例外）→ {(年, BIB, 項目)}"""
    out = {}
    for fn in glob.glob(os.path.join(REPO, 'etl', 'rules', 'events', '*.json')):
        d = json.load(open(fn, encoding='utf-8'))
        for x in d.get('recompute_exceptions') or []:
            out[(str(d.get('year') or d.get('season')), x.get('bib'), x.get('item'))] = x.get('basis')
    return out


EXC_ITEM = {'id_air': 'Air Total', 'id_turns': 'Turns Total', 'id_time': 'Time Points', 'id_score': 'Score'}


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
    issues = []
    checked = collections.Counter()
    for i, (rid, rr) in enumerate(sorted(by_round.items())):
        rnd = rounds[rid]
        src = rr[0]['provenance']['parser_version']
        if src.startswith('moguls-results'):
            issues += check_upstream(rr, up)
            issues += check_pdf_round(rr, fis_style=True)
            checked['C+A(fis)'] += len(rr)
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
        if k in EXC_ITEM and r['series'] == 'SAJ_AJ':
            key = (r['date'][:4], r['bib'], EXC_ITEM[k])
            if key in exc:
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
