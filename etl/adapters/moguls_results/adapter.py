"""moguls_results アダプタ: ナショナルチーム用 moguls-results の公開データ（data/*.json）を得点段階（score）で取り込む。

W杯・世界選手権・五輪は moguls-results 側で審判点まで多層照合済み。ここでは PDF を読み直さず、
公開 JSON から順位・得点・タイム・ターン点・エア点・タイム点だけを写す（審判ごとの点は持たない）。
元 PDF の SHA-256 は moguls-results の記録をそのまま registry に持ち、build がファイルと照合する。

registry は sync_registry.py が moguls-results の manifest から生成する（手で書かない）。
"""
import json, os
from ... import config

SITE = config.MOGULS_RESULTS_SITE
_cache = {}


def _load(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def upstream():
    """moguls-results の公開データを一度だけ読む: {'manifest', 'events': {event_id: event}, 'runs': {round_id: [run]}, 'rules'}."""
    if _cache:
        return _cache
    data_dir = os.path.join(config.MOGULS_RESULTS_REPO, 'data')
    manifest = _load(os.path.join(data_dir, 'manifest.json'))
    events = {e['event_id']: e for e in _load(os.path.join(data_dir, manifest['files']['events']))}
    runs = {}
    for season, fn in manifest['files']['runs'].items():
        for run in _load(os.path.join(data_dir, fn)):
            runs.setdefault(run['round_id'], []).append(run)
    rules = _load(os.path.join(data_dir, manifest['files']['rules']))
    _cache.update({'manifest': manifest, 'events': events, 'runs': runs, 'rules': rules, 'data_dir': data_dir})
    return _cache


def pdf_path(rel):
    """moguls-results の PDF は 全試合のリザルト（読むだけ）か、moguls-results/source_pdfs にある。"""
    for root in (config.MOGULS_PDF_ROOT, os.path.join(config.MOGULS_RESULTS_REPO, 'source_pdfs')):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return os.path.join(config.MOGULS_PDF_ROOT, rel)


def event_url(event_id):
    return f"{SITE}event.html?id={event_id}"


def load_rules(version):
    rs = _load(os.path.join(config.RULES_DIR, 'rulesets.json'))
    if version not in rs['versions']:
        raise KeyError(f"rulesets.json に版 {version} が無い")
    return rs['versions'][version]


def to_record(run):
    return {
        'rank': run['rank'], 'bib': run['bib'], 'athlete_id': run['athlete_id'], 'fis_code': run.get('fis_code'),
        'saj_no': None, 'name': run['name'], 'noc': run.get('noc'), 'yb': run.get('yb'),
        'affiliation': None, 'club': None, 'status': run['status'], 'reserve_judge': run.get('reserve_judge', False),
        'seconds': run.get('seconds'), 'time_points': run.get('time_points'), 'air_total': run.get('air_total'),
        'turns_total': run.get('turns_total'), 'run_score': run.get('run_score'),
        'tie': run.get('tie'), 'counting': run.get('counting', True), 'q_block': run.get('q_block'),
        'best_score': run.get('best_score'), 'page': (run.get('provenance') or {}).get('page'),
    }


def load_event(ev, imported_at, log=None):
    up = upstream()
    e = up['events'].get(ev['event_id'])
    if e is None:
        return [{'error_only': True, 'event_id': ev['event_id'], 'message': 'moguls-results の公開データにこの大会が無い（sync_registry.py を再実行）'}]
    ctxs = []
    for r in e['rounds']:
        src = r['source']
        rules_version = src.get('rules_version') or ev['rules']
        rules = load_rules(rules_version)
        cls = {
            'event_id': ev['event_id'], 'round_id': r['round_id'], 'season': ev['season'], 'series': ev['series'],
            'grade': ev.get('grade'), 'discipline': ev.get('discipline', 'MO'), 'gender': r['gender'], 'round': r['round'],
            'round_text': r.get('round_text'), 'codex': r.get('codex'), 'tier': ev.get('tier', 'score'),
            'panel': {'turns': rules['judges']['turns'], 'air': rules['judges']['air']},
            'rel': 'moguls-results/' + src['pdf'], 'path': pdf_path(src['pdf']), 'pdf_sha256': src['pdf_sha256'],
            'pages': None, 'url': src.get('fis_url'), 'page_url': event_url(ev['event_id']), 'rules_version': rules_version,
            'name_ja': ev.get('name_ja'), 'format': ev.get('format'),
            'upstream': {'site': 'moguls-results', 'url': event_url(ev['event_id']) + '#' + r['round_id'],
                         'tier': 'detail', 'verification': r.get('verification'), 'data_version': up['manifest']['dataVersion']},
        }
        course = r.get('course') or {}
        meta = {
            'date': r.get('date'), 'date_text': r.get('date_text'), 'start_time': r.get('start_time'), 'venue': r.get('venue'),
            'judges': [{'judge_no': j['no'], 'role': j['role'], 'name': j['name'], 'noc': j.get('noc')} for j in r.get('judges', [])],
            'pace_time': r.get('pace_time'), 'num_competitors': r.get('n_competitors'),
            'course_name': course.get('name'), 'course_length_m': course.get('length_m'), 'course_width_m': course.get('width_m'),
            'gate_width_m': course.get('gate_width_m'), 'gradient_deg': course.get('gradient_deg'),
            'officials': r.get('officials', []), 'q_layout': r.get('q_layout'),
            'parser_version': f"moguls-results {src.get('parser_version') or ''}".strip(), 'report_created': src.get('report_created'),
        }
        records = [to_record(run) for run in up['runs'].get(r['round_id'], [])]
        ctxs.append({'cls': cls, 'meta': meta, 'records': records, 'rules': rules, 'findings': [], 'ab_compared': False})
    return ctxs
