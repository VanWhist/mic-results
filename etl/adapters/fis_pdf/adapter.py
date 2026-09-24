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
            'discipline': ev.get('discipline', 'MO'), 'gender': gender, 'round': code, 'round_text': meta_a.get('round'),
            'codex': codex, 'tier': tier,
            'panel': {'turns': rules['judges']['turns'], 'air': rules['judges']['air']},
            'rel': pdf['path'], 'path': path, 'pdf_sha256': pdf.get('sha256'), 'url': pdf.get('url'), 'page_url': pdf.get('page_url'),
            'pages': None, 'name_ja': ev.get('name_ja'), 'format': ev.get('format'), 'rules_version': ev['rules'],
        }
        meta = dict(meta_a)
        meta['date_text'] = meta_a.get('date')
        meta['date'] = iso_date(meta_a.get('date'))
        records = [to_record(r) for r in recs_a]
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
                    findings += layer1(round_id, recs_a, recs_b, meta_a, meta_b)
                    ab = True
        ctxs.append({'cls': cls, 'meta': meta, 'records': records, 'findings': findings, 'ab_compared': ab, 'rules': rules})
        log(f"  {round_id}: {len(records)} 記録")
    return ctxs
