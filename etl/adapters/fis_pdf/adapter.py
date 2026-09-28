"""fis_pdf アダプタ: FIS 様式のリザルト PDF（国内 FIS レース・ANC・WJC・EC・NAC・AC など）。

moguls-results と同じ2方式（parser_a = 座標帯、parser_b = 行）で読み、第1層で全項目を突き合わせる。
registry の pdfs[] は1件＝1ラウンド（round・gender・codex を持つ）。
規則は etl/rules/rulesets.json の版名（例 "2025-26"）を registry の rules に書く。
古い年の PDF に審判ごとの点が無いときは tier を "score" にする（A だけ読み、第1・2層は対象外）。
"""
import json, os, re
from ... import config
from ...verify import Finding, layer1
from . import parser_a
try:
    from . import parser_b
    PARSER_B_ERROR = None
except Exception as e:  # noqa
    parser_b = None
    PARSER_B_ERROR = repr(e)

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


def load_event(ev, imported_at, log=print):
    """registry の 1 大会 → [ctx]。pdfs[] の各要素: path, sha256, url, page_url, round, gender, codex"""
    rules = load_rules(ev['rules'])
    tier = ev.get('tier', 'detail')
    ctxs = []
    for pdf in ev['pdfs']:
        path = pdf_path(pdf['path'])
        findings = []
        try:
            meta_a, recs_a = parser_a.parse_moguls_results(path)
        except Exception as e:  # noqa
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"パーサ A 例外 {pdf['path']}: {e!r}"})
            continue
        code = pdf['round']
        if meta_a.get('q_layout') and code == 'Q':
            code = 'Q2'  # 予選2 の報告書は Q1/Q2 二段で印字される
        gender = pdf.get('gender') or ({"Men's Moguls": 'M', "Ladies' Moguls": 'W', "Women's Moguls": 'W'}.get(meta_a.get('event')))
        codex = pdf.get('codex') or meta_a.get('codex')
        round_id = f"{ev['event_id']}-{gender}-{code}"
        cls = {
            'event_id': ev['event_id'], 'round_id': round_id, 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
            'discipline': ev.get('discipline', 'MO'), 'gender': gender, 'round': code, 'round_text': round_text_of(meta_a.get('round'), code),
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
        # ターン審判の人数はラウンドごとに印字から決める（W杯・EC は 5 人、世界ジュニア 2022・ANC は 3 人の年がある）
        n_turns, why = turns_panel(recs_a)
        rules_r = dict(rules)
        if n_turns and n_turns != rules['judges']['turns']:
            trim_panel(recs_a, n_turns)
            rules_r = dict(rules, judges=dict(rules['judges'], turns=n_turns), discard_high_low=n_turns >= 5)
            cls['panel'] = {'turns': n_turns, 'air': rules['judges']['air']}
            if why == 'zero':
                findings.append(Finding('warning', round_id, 'layer0', f"ターン審判 {n_turns} 人の表を 5 列の様式で印字（J{n_turns + 1} 以降は全員 0.0）。{n_turns} 人として計算"))
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
        ab = False
        if tier == 'detail':
            if parser_b is None:
                findings.append(Finding('error', round_id, 'layer1', f"パーサ B を読み込めない: {PARSER_B_ERROR}"))
            else:
                try:
                    meta_b, recs_b = parser_b.parse_moguls_results(path)
                except Exception as e:  # noqa
                    findings.append(Finding('error', round_id, 'layer1', f"パーサ B 例外: {e!r}"))
                else:
                    if n_turns:
                        trim_panel(recs_b, n_turns)
                    findings += layer1(round_id, recs_a, recs_b, meta_a, meta_b)
                    ab = True
        ctxs.append({'cls': cls, 'meta': meta, 'records': records, 'findings': findings, 'ab_compared': ab, 'rules': rules_r})
        log(f"  {round_id}: {len(records)} 記録")
    return ctxs
