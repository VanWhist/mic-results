"""fis_pdf アダプタ: FIS 様式のリザルト PDF（国内 FIS レース・ANC・WJC・EC・NAC・AC など）。

moguls-results と同じ2方式（parser_a = 座標帯、parser_b = 行）で読み、第1層で全項目を突き合わせる。
registry の pdfs[] は1件＝1ラウンド（round・gender・codex を持つ）。
規則は etl/rules/rulesets.json の版名（例 "2025-26"）を registry の rules に書く。
古い年の PDF に審判ごとの点が無いときは tier を "score" にする（A だけ読み、第1・2層は対象外）。
"""
import collections, json, os, re
from ... import config, scoring
from ...verify import Finding, layer1
from . import parser_a
try:
    from . import parser_b
    PARSER_B_ERROR = None
except Exception as e:  # noqa
    parser_b = None
    PARSER_B_ERROR = repr(e)
# 文字表（「=== ===」の等幅の表。カナダの集計ソフト Winfree。NAC 2022 Apex・Val St-Côme）は別の 2 方式で読む。
# registry の pdfs[] に layout: "ascii" があるもの
from . import ascii_a, ascii_b

MONTHS = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}


def iso_date(fis_date):
    """'SAT 30 NOV 2024' -> '2024-11-30'"""
    m = re.search(r'(\d{1,2})\s+([A-Z]{3})\s+(\d{4})', fis_date or '')
    if not m:
        return None
    return f"{int(m.group(3)):04d}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def load_rules(version):
    with open(os.path.join(config.RULES_DIR, 'rulesets.json'), encoding='utf-8') as fh:
        rs = json.load(fh)
    if version not in rs['versions']:
        raise KeyError(f"rulesets.json に版 {version} が無い")
    return rs['versions'][version]


def athlete_id_of(rec):
    if rec.get('fis_code'):
        return str(rec['fis_code'])
    return 'x-' + config.slug(rec.get('name', '')) + '-' + config.slug(rec.get('noc', ''))


def to_record(rec):
    out = dict(rec)
    out['athlete_id'] = athlete_id_of(rec)
    out.setdefault('saj_no', None)
    out.setdefault('affiliation', None)
    out.setdefault('club', None)
    return out


def turns_panel(recs):
    """実際に採点したターン審判の列だけを残す。戻り値: (審判数, 落とした列の理由)。
    - 3 人審判の表（世界ジュニア 2022 など）は、パーサ A が J4・J5 の帯を空（None）で返す
    - 3 人審判を 5 列の様式で印字した表（ANC 2026 など）は、全員の J4・J5 が 0.0（合計は 3 人分の和）"""
    ok = [r for r in recs if r.get('status') == 'OK' and r.get('base_scores')]
    if not ok:
        return None, None
    width = max(len(r['base_scores']) for r in ok)
    keep = width
    reason = None
    while keep > 1:
        col = [r['base_scores'][keep - 1] if len(r['base_scores']) >= keep else None for r in ok]
        dcol = [(r.get('ded_scores') or [None] * width)[keep - 1] if len(r.get('ded_scores') or []) >= keep else None for r in ok]
        if all(v is None for v in col) and all(v is None for v in dcol):
            keep -= 1
            reason = reason or 'empty'
        elif all(v == 0 for v in col) and all(v in (0, None) for v in dcol) and keep > 3:
            keep -= 1
            reason = 'zero'
        else:
            break
    return keep, (reason if keep < width else None)


def trim_panel(recs, n):
    for r in recs:
        if r.get('base_scores'):
            r['base_scores'] = r['base_scores'][:n]
        if r.get('ded_scores'):
            r['ded_scores'] = r['ded_scores'][:n]


RE_ROUND_WORD = re.compile(r'(Super Final|Qualification\s*\d?|Final\s*\d?)', re.I)
RE_FOOTER_VENUE = re.compile(r'\d{4}\s*/\s*([^/\n]+?\([A-Z]{3}\))\s*/\s*\d{3,5}\b')


def round_text_of(raw, code):
    """表示用のラウンド名。主催者の様式で見出しの位置が違い、印字から取った文字列に会場名や
    「Start Time: tba」が混ざる（2017 NAC・世界ジュニア）ので、ラウンドの語だけを取り出す。取れなければ既定名"""
    m = RE_ROUND_WORD.search(raw or '')
    if m:
        return re.sub(r'\s+', ' ', m.group(1)).strip().title()
    return config.ROUND_TEXT_DEFAULT.get(code, code)


def venue_of(path, meta, ev):
    """会場。どの様式にもあるフッタ「<日付> / <会場 (国)> / <codex>」を優先する（見出しの会場行は様式でまちまち:
    ユニバーシアード 2025 は無し、世界ジュニア 2025 は「2025 FIS」を拾う）"""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        m = RE_FOOTER_VENUE.search(pdf.pages[0].extract_text() or '')
    return (m.group(1).strip() if m else None) or meta.get('venue') or ev.get('place')


def pdf_path(rel):
    return rel if os.path.isabs(rel) else os.path.join(config.PDF_ROOT, rel)


# 総合（OVERALL）の報告の区切り → その区切りで終わった選手の走りを、1 行目（最後の走り）から順に割り当てるラウンド
SECTION_CODES = {'Final 3': ['F3', 'F2', 'F1', 'Q'], 'Super Final': ['F3', 'F2', 'F1', 'Q'],
                 'Final 2': ['F2', 'F1', 'Q'], 'Final 1': ['F1', 'Q'], 'Final': ['F1', 'Q'],
                 'Qualification': ['Q'], 'Qualification 1': ['Q1'], 'Qualification 2': ['Q2', 'Q1']}
# 走りの印（F2: / F1 / Q1: など）→ ラウンド。総合の報告の「Q1」は予選（1 本）の印
LABEL_CODE = {'F3': 'F3', 'SF': 'F3', 'F2': 'F2', 'F1': 'F1', 'Q1': 'Q', 'Q': 'Q'}


LADDER = ['F3', 'F2', 'F1', 'Q']


def athlete_runs(recs):
    """総合の報告の記録を選手ごとの走りの並びにまとめる（1 行目 = block_index 0 から次の選手の前まで）"""
    out = []
    for r in recs:
        if (r.get('block_index') or 0) == 0 or not out:
            out.append([r])
        else:
            out[-1].append(r)
    return out


def codes_for(section, runs):
    """1 人の選手の走り（1 行目 = 最後の走りから順）に割り当てるラウンドと、その選手が滑ったはずのラウンド。
    戻り値: (割り当て, 滑ったはずのラウンド) / 区切りを知らなければ None。
    走りの印（F2: / F1: / Q1:）が全部あって上から順に並んでいれば印どおり。ANC 2019 は「Final」の区切りが決勝 2 本
    （F1・F2、良い方で順位）で、男子は予選の走りも載る（3 本）が女子は載らない（2 本）。印が無ければ区切りから決め、
    区切りが示すより走りが多いときは上のラウンドへ広げる"""
    base = SECTION_CODES.get(section)
    if base is None:
        return None
    labels = [LABEL_CODE.get(r.get('run_label')) for r in runs]
    if base[-1] == 'Q' and all(labels) and all(c in LADDER for c in labels) \
            and all(LADDER.index(a) < LADDER.index(b) for a, b in zip(labels, labels[1:])):
        return labels, LADDER[LADDER.index(labels[0]):]
    if len(runs) > len(base) and base[-1] == 'Q':
        return LADDER[-len(runs):], LADDER[-len(runs):]
    return base, base


def split_overall(recs, meta, label):
    """総合の報告の記録（パーサ A または B）をラウンドごとに分ける。戻り値: ({code: [rec]}, [問題の文言])。
    区切りの無い報告（ANC 2025 は予選だけ、世界ジュニア 2019 の RLF は決勝 1 だけ）は 1 つのラウンド"""
    head = ' '.join((meta.get('raw_header') or [])[:6]) + ' ' + (meta.get('round') or '')
    # 見出しの無い予選の報告（ANC 2025）は表の区切り「Qualified to Final」が先頭の数行に入る。これは決勝の報告の印ではない
    head = re.sub(r'qualified to final', ' ', head, flags=re.I)
    single ='F1' if re.search(r'\bfinal\b', head, re.I) and not re.search(r'overall', head, re.I) else 'Q'
    groups, problems = collections.OrderedDict(), []
    for runs in athlete_runs(recs):
        sec = runs[0].get('section')
        codes = (codes_for(sec, runs) or (None,))[0] if sec else [single]
        if codes is None:
            problems.append(f"{label}: 区切り「{sec}」を知らない（{runs[0].get('name')}）")
            continue
        if len(runs) > len(codes):
            problems.append(f"{label}: {runs[0].get('name')} の走りが多すぎる（区切り {sec}、{len(runs)} 本）")
            continue
        for r, code in zip(runs, codes):
            lab = r.get('run_label')
            if lab in LABEL_CODE and LABEL_CODE[lab] != code:
                problems.append(f"{label}: {r.get('name')} の走りの印 {lab} と区切りからの割り当て {code} が合わない")
            groups.setdefault(code, []).append(r)
    if best_of_check(recs, '')[0]:
        merge_best_of(groups, recs)
    return groups, problems


def merge_best_of(groups, recs):
    """決勝が 2 本で良い方（Best Score）で順位が付く様式（ANC 2019）: 印の F1（1 本目）・F2（2 本目）は勝ち抜きのラウンドではない
    ので、1 つの決勝ラウンド（F1）にまとめる。走りには q_block 'R1'（1 本目）・'R2'（2 本目）を付け、順位と Best Score は選手の
    1 行目の印字（最終順位）を両方の走りに持たせる。良い方の走りを採用（counting）、もう 1 本は採用外（選手ページには出ない）"""
    if 'F1' not in groups or 'F2' not in groups:
        return
    head = {}
    for runs in athlete_runs(recs):
        head[runs[0]['bib']] = runs[0]
    by_bib = collections.OrderedDict()
    for code, qb in (('F2', 'R2'), ('F1', 'R1')):
        for r in groups[code]:
            r['q_block'] = qb
            by_bib.setdefault(r['bib'], []).append(r)
    merged = []
    for bib in [b for b in head if b in by_bib]:
        pair, h = by_bib[bib], head[bib]
        ok = [r for r in pair if r.get('status') == 'OK' and r.get('run_score') is not None]
        best = max(ok, key=lambda r: r['run_score']) if ok else pair[0]
        for r in pair:
            r['rank'], r['best_score'], r['counting'] = h.get('rank'), h.get('best_score'), r is best
        merged += pair
    groups['F1'] = merged
    del groups['F2']


def partial_codes(recs):
    """総合の報告に、先のラウンドへ進んだ選手の前の走りが載っていないラウンド（NAC 2019 Stratton は決勝 2 の 6 名の決勝 1・予選が無い）。
    戻り値: {code: その区切りより上の区切りで終わった選手のうち、このラウンドの走りが無い人数}"""
    expected, got = collections.defaultdict(set), collections.defaultdict(set)
    for runs in athlete_runs(recs):
        codes, should = codes_for(runs[0].get('section'), runs) or ([], [])
        for c in should:
            expected[c].add(runs[0]['bib'])
        for r, c in zip(runs, codes):
            got[c].add(r['bib'])
    return {c: len(expected[c] - got[c]) for c in expected if expected[c] - got[c]}


def best_of_check(recs_a, round_id):
    """決勝の順位を Best Score（決勝の走りの良い方）で付ける様式か（ANC 2019）。その様式なら、決勝の区切りの選手の印字の
    順位は総合の順位でラウンドの順位ではない。総合の順位が Best Score の順に並んでいるかを確かめる。戻り値: (様式か, 問題)"""
    first = [r for r in recs_a if (r.get('block_index') or 0) == 0 and r.get('status') == 'OK'
             and r.get('section') and r['section'] != 'Qualification']
    best_of = any(r.get('best_score') is not None and r.get('run_score') is not None
                  and abs(r['best_score'] - r['run_score']) > 1e-9 for r in first)
    f = []
    if best_of:
        ranked = sorted([r for r in first if r.get('rank')], key=lambda r: r['rank'])
        for x, y in zip(ranked, ranked[1:]):
            if (x.get('best_score') or 0) < (y.get('best_score') or 0):
                f.append(Finding('error', round_id, 'layer3', f"総合の順位が Best Score の順でない: {x['name']} {x['rank']}位 {x['best_score']} / {y['name']} {y['rank']}位 {y['best_score']}"))
    return best_of, f


def fill_overall_ranks(round_id, recs, rules):
    """総合の報告から組み立てたラウンドの順位。印字があるのは各選手の最後の走り（1 行目）だけなので、得点から規則どおり
    （ICR 4207.3 の同点処理を含む）に並べ、印字のある順位と一致するか確かめてから、印字の無い分を埋める"""
    f = []
    ok =[r for r in recs if r.get('status') == 'OK' and r.get('run_score') is not None and r.get('turns_total') is not None]
    items = [{'run_score': scoring.D(r['run_score']), 'turns_total': scoring.D(r['turns_total']),
              'air_without_dd': scoring.air_without_dd(r['air_jumps']), 'seconds': scoring.D(r['seconds'] or 0), '_r': r}
             for r in ok]
    n_filled = 0
    for it, rank in scoring.rank_order(items, rules):
        r = it['_r']
        if r.get('rank') is not None:
            if r['rank'] != rank:
                f.append(Finding('error', round_id, 'layer3', f"{r['name']} 総合の報告の順位 {r['rank']} と得点順 {rank} が合わない"))
        else:
            r['rank'] = rank
            r['rank_computed'] = True
            n_filled += 1
    if n_filled:
        f.append(Finding('warning', round_id, 'layer3',
                         f"総合の報告から組み立てたラウンド。次のラウンドに進んだ {n_filled} 名の順位は印字が無いので、得点から規則どおりに並べた"))
    return f


def drop_nonstarters(round_id, recs, meta):
    """文字表の決勝の報告は、前のラウンドの全員を載せ、進めなかった選手を「dns」で並べる（NAC 2022 Apex）。印字の
    「Cutoff: N」（進出人数）と、dns 以外の人数が一致するときだけ dns の行を除く。一致しなければ除かずに止める"""
    n = meta.get('cutoff')
    dns = [r for r in recs if r['status'] == 'DNS']
    if not n or not dns:
        return []
    started = len(recs) - len(dns)
    if started != n:
        return [Finding('error', round_id, 'layer0', f"進出人数の印字 Cutoff {n} と、dns 以外の人数 {started} が合わない（dns の行を除けない）")]
    recs[:] = [r for r in recs if r['status'] != 'DNS']
    return [Finding('warning', round_id, 'layer0', f"進めなかった {len(dns)} 名が dns で並ぶ様式。進出人数の印字 Cutoff {n} と一致したので除いた")]


def load_event(ev, imported_at, log=print):
    """registry の 1 大会 → [ctx]。pdfs[] の各要素: path, sha256, url, page_url, round, gender, codex。
    round が 'overall' の PDF（総合の報告）からは、区切りと走りの並びでラウンドを組み立てる（同じ性別で別の報告書がある
    ラウンドは除く）"""
    rules = load_rules(ev['rules'])
    tier = ev.get('tier', 'detail')
    own = {(p.get('gender'), p['round']) for p in ev['pdfs'] if p['round'] != 'overall'}
    ctxs = []
    for pdf in ev['pdfs']:
        path = pdf_path(pdf['path'])
        ascii_layout = pdf.get('layout') == 'ascii'
        pa, pb = (ascii_a, ascii_b) if ascii_layout else (parser_a, parser_b)
        try:
            meta_a, recs_a = pa.parse_moguls_results(path)
        except Exception as e:  # noqa
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"パーサ A 例外 {pdf['path']}: {e!r}"})
            continue
        meta_b, recs_b, b_error = None, None, None
        if tier == 'detail':
            if pb is None:
                b_error = f"パーサ B を読み込めない: {PARSER_B_ERROR}"
            else:
                try:
                    meta_b, recs_b = pb.parse_moguls_results(path)
                except Exception as e:  # noqa
                    b_error = f"パーサ B 例外: {e!r}"
        gender = pdf.get('gender') or ({"Men's Moguls": 'M', "Ladies' Moguls": 'W', "Women's Moguls": 'W'}.get(meta_a.get('event')))
        if pdf['round'] != 'overall':
            code = pdf['round']
            if meta_a.get('q_layout') and code == 'Q':
                code = 'Q2'  # 予選2 の報告書は Q1/Q2 二段で印字される
            pre, rt = [], None
            if ascii_layout:
                round_id = f"{ev['event_id']}-{gender}-{code}"
                pre += drop_nonstarters(round_id, recs_a, meta_a)
                if recs_b is not None:
                    drop_nonstarters(round_id, recs_b, meta_b)
                for p in meta_a.get('unparsed_lines') or []:
                    pre.append(Finding('error', round_id, 'layer1', f"A が読めない行 p{p[0]}: {p[1]}"))
                for p in (meta_b or {}).get('unparsed_lines') or []:
                    pre.append(Finding('error', round_id, 'layer1', f"B が読めない行 p{p[0]}: {p[1]}"))
                # 見出しのラウンド名は様式でまちまち（「Moguls Run 1」「Qualif+finale 16 Descente 1」）なので既定名にする
                rt = config.ROUND_TEXT_DEFAULT.get(code, code)
            ctxs.append(make_ctx(ev, pdf, path, code, gender, recs_a, recs_b, meta_a, meta_b, b_error, rules, tier, pre, log,
                                 round_text=rt))
            continue
        # 区切りの無い報告の種類（予選か決勝か）は文書の見出しで決める。A・B とも同じ判定を使う
        ga, prob_a = split_overall(recs_a, meta_a, 'A')
        gb, prob_b = split_overall(recs_b, meta_a, 'B') if recs_b is not None else ({}, [])
        missing = partial_codes(recs_a)
        _, bo_find = best_of_check(recs_a, f"{ev['event_id']}-{gender}")
        lowest = [c for c in config.ROUND_ORDER if c in ga][:1]
        for code, grp in ga.items():
            if (gender, code) in own:
                continue
            round_id = f"{ev['event_id']}-{gender}-{code}"
            extra = [Finding('error', round_id, 'layer1', p) for p in prob_a + prob_b]
            ma, mb = dict(meta_a), (dict(meta_b) if meta_b is not None else None)
            partial = missing.get(code, 0)
            if code not in lowest or partial:
                # 表頭の出場人数は全員の数。決勝の人数は印字が無い（勝ち上がり人数は第 3 層で確かめる）
                ma['num_competitors'] = None
                if mb is not None:
                    mb['num_competitors'] = None
            rt = config.ROUND_TEXT_DEFAULT.get(code, code)
            if partial:
                rt += f"（上位 {partial} 名を除く）"
            merged = any(r.get('q_block') in ('R1', 'R2') for r in grp)
            if merged:
                # 決勝は同じ選手が 2 本滑り良い方で順位が付く（ANC 2019）。2 本を 1 ラウンドにまとめてある（merge_best_of）
                rt = 'Final（2 本の良い方）'
            ctx = make_ctx(ev, pdf, path, code, gender, grp, gb.get(code, []) if recs_b is not None else None,
                           ma, mb, b_error, rules, tier, extra, log, round_text=rt)
            if partial:
                # 一部のラウンド: 載っているのはこの区切りで終わった選手だけで、順位は総合の順位（印字の最小順位から）。
                # 第 3 層は rank_group=2 で印字の最小順位から検算する。勝ち上がりの検算からは外す
                ctx['cls']['partial'] = True
                for r in ctx['records']:
                    r['rank_group'] = 2
                ctx['findings'].append(Finding('warning', round_id, 'layer0',
                                               f"総合の報告に、先のラウンドへ進んだ {partial} 名のこのラウンドの走りが載っていない。載っている選手だけのラウンドとして公開"))
            elif merged:
                # 順位は印字の最終順位（良い方の順）。第 3 層が良い方の走りで並べ直して確かめる
                ctx['findings'] += [Finding(x.level, round_id, x.layer, x.message) for x in bo_find]
                ctx['findings'].append(Finding('warning', round_id, 'layer3',
                                               '決勝は 2 本滑り良い方（Best Score）で順位が付く様式。2 本を 1 ラウンドにまとめ、良い方を採用の走りとした'))
            else:
                ctx['findings'] += fill_overall_ranks(round_id, ctx['records'], ctx['rules'])
            ctxs.append(ctx)
    return ctxs


def make_ctx(ev, pdf, path, code, gender, recs_a, recs_b, meta_a, meta_b, b_error, rules, tier, findings, log, round_text=None):
    """1 ラウンド分の ctx。審判の人数・例外・2 方式の突き合わせ（第 1 層）"""
    findings = list(findings)
    codex = pdf.get('codex') or meta_a.get('codex')
    round_id = f"{ev['event_id']}-{gender}-{code}"
    cls = {
        'event_id': ev['event_id'], 'round_id': round_id, 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
        'discipline': ev.get('discipline', 'MO'), 'gender': gender, 'round': code,
        'round_text': round_text or round_text_of(meta_a.get('round'), code),
        'codex': codex, 'tier': tier,
        'panel': {'turns': rules['judges']['turns'], 'air': rules['judges']['air']},
        'rel': pdf['path'], 'path': path, 'pdf_sha256': pdf.get('sha256'), 'url': pdf.get('url'), 'page_url': pdf.get('page_url'),
        'pages': None, 'name_ja': ev.get('name_ja'), 'format': ev.get('format'), 'rules_version': ev['rules'],
    }
    meta = dict(meta_a)
    meta['venue'] = venue_of(path, meta_a, ev)
    # registry の competitor_count_exceptions: 表頭の出場人数の印字が、表の人数・FIS 公式サイトの結果と違うもの（根拠つき）
    for exc in ev.get('competitor_count_exceptions') or []:
        if exc.get('round_id') == round_id and meta.get('num_competitors') == exc.get('printed'):
            meta['num_competitors'] = exc['actual']
            findings.append(Finding('warning', round_id, 'layer0',
                                    f"出走数の印字 {exc['printed']} を {exc['actual']} として扱う（例外。根拠: {exc['basis']}）"))
    meta['date_text'] = meta_a.get('date')
    meta['date'] = iso_date(meta_a.get('date'))
    # registry の date_fallback: 見出しが無く日付が印字されていない PDF（ANC 2025）の日付。FIS の大会ページの日程（根拠つき）
    fb = next((x for x in ev.get('date_fallback') or [] if x.get('codex') == codex), None)
    if meta['date'] is None and fb:
        meta['date'], meta['date_text'] = fb['date'], fb['date']
        findings.append(Finding('warning', round_id, 'layer0', f"日付の印字が無いので {fb['date']} とした（根拠: {fb['basis']}）"))
    # ターン審判の人数はラウンドごとに印字から決める（W杯・EC は 5 人、世界ジュニア 2022・ANC は 3 人の年がある）
    n_turns, why = turns_panel(recs_a)
    rules_r = dict(rules)
    if n_turns and n_turns != rules['judges']['turns']:
        trim_panel(recs_a, n_turns)
        rules_r = dict(rules, judges=dict(rules['judges'], turns=n_turns), discard_high_low=n_turns >= 5)
        cls['panel'] = {'turns': n_turns, 'air': rules['judges']['air']}
        if why == 'zero':
            findings.append(Finding('warning', round_id, 'layer0', f"ターン審判 {n_turns} 人の表を 5 列の様式で印字（J{n_turns + 1} 以降は全員 0.0）。{n_turns} 人として計算"))
    ab = False
    if tier == 'detail':
        if b_error:
            findings.append(Finding('error', round_id, 'layer1', b_error))
        else:
            if n_turns:
                trim_panel(recs_b, n_turns)
            findings += layer1(round_id, recs_a, recs_b, meta_a, meta_b)
            ab = True
    records = [to_record(r) for r in recs_a]
    # registry の recompute_exceptions: 規則どおりに再計算した値と印字が違うが、印字が公式と確かめたもの（根拠つき）。
    # 例: ユニバーシアード 2017 男子予選 JONES（計時なし 0.00 秒 → タイム点 0 と印字、式どおりなら 20）
    for exc in ev.get('recompute_exceptions') or []:
        if exc.get('round_id') != round_id:
            continue
        hit = [r for r in records if str(r.get('bib')) == str(exc.get('bib'))]
        if len(hit) != 1:
            findings.append(Finding('error', round_id, 'layer2', f"recompute_exceptions の bib {exc.get('bib')} が見つからない（登録を見直す）"))
            continue
        hit[0].setdefault('exceptions', {})[exc['field']] = {'printed': exc['printed'], 'calc': exc['calc'], 'basis': exc['basis']}
    log(f"  {round_id}: {len(records)} 記録")
    return {'cls': cls, 'meta': meta, 'records': records, 'findings': findings, 'ab_compared': ab, 'rules': rules_r}
