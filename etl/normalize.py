"""Turn adapter records into the published data model (rounds, runs) with recomputed values.

Every adapter hands over the same shapes:
  cls  : event_id, season, series, grade, discipline, gender, round (Q/Q1/Q2/F1/F2/F3), round_text,
         codex, tier (detail/score/rank), panel {turns, air, air_judge_nos}, rel (PDF path relative to
         PDF_ROOT), path, pages, name_ja, format (dict with 'advance' per gender), rules_version
  meta : date (ISO), date_text, venue, judges [{judge_no, role, name, noc}], pace_time, num_competitors,
         course_*, parser_version, officials
  rec  : rank, bib, athlete_id, fis_code, saj_no, name, noc, yb, affiliation, club, status, seconds,
         time_points, air_jumps [{J6, J7, jump, DD}], air_total, base_scores, ded_scores (negative),
         base_total, ded_total, turns_total, run_score, tie, counting, q_block, best_score, page,
         reserve_judge
"""
import hashlib
from decimal import Decimal
from . import scoring
from .config import slug, ROUND_TEXT_DEFAULT


def _num_eq(a, b, tol=Decimal('0.005')):
    if a is None or b is None:
        return False
    return abs(Decimal(str(a)) - Decimal(str(b))) <= tol


def f2(x):
    """Decimal/float -> float with at most 2 decimals (JSON)."""
    if x is None:
        return None
    return float(scoring.trunc(x)) if isinstance(x, Decimal) else float(x)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def judge_id(name, noc):
    # 日本語名は slug() で消えるので、空白を除いた氏名をそのまま使う（画面には出さない内部 ID）
    core = slug(name)
    if core == 'x':
        core = ''.join((name or '').split())
    return 'j-' + core + ('-' + str(noc).lower() if noc else '')


def make_round(cls, meta, records, rules, imported_at):
    round_id = cls.get('round_id') or f"{cls['event_id']}-{cls['gender']}-{cls['round']}"
    judges = [{'no': j['judge_no'], 'role': j['role'], 'judge_id': judge_id(j['name'], j.get('noc')),
               'name': j['name'], 'noc': j.get('noc')} for j in meta.get('judges', [])]
    rnd = {
        'round_id': round_id, 'event_id': cls['event_id'], 'codex': cls.get('codex'), 'season': cls['season'],
        'series': cls['series'], 'grade': cls.get('grade'), 'discipline': cls.get('discipline', 'MO'),
        'gender': cls['gender'], 'round': cls['round'],
        'round_text': cls.get('round_text') or ROUND_TEXT_DEFAULT.get(cls['round'], cls['round']),
        'tier': cls['tier'], 'panel': cls.get('panel'),
        'date': meta.get('date'), 'date_text': meta.get('date_text'), 'start_time': meta.get('start_time'),
        'venue': meta.get('venue'), 'n_competitors': meta.get('num_competitors'),
        'pace_time': meta.get('pace_time'),
        'course': {'name': meta.get('course_name'), 'length_m': meta.get('course_length_m'), 'width_m': meta.get('course_width_m'),
                   'gate_width_m': meta.get('gate_width_m'), 'gradient_deg': meta.get('gradient_deg')},
        'judges': judges, 'officials': meta.get('officials', []),
        'q_layout': bool(meta.get('q_layout')),
        'source': {'pdf': cls['rel'], 'pdf_sha256': cls.get('pdf_sha256') or sha256_file(cls['path']), 'url': cls.get('url'),
                   'page_url': cls.get('page_url'), 'pages': cls.get('pages'), 'fis_url': cls.get('fis_url'),
                   'report_created': meta.get('report_created'), 'imported_at': imported_at,
                   'parser_version': meta.get('parser_version'), 'rules_version': cls.get('rules_version'),
                   # 別サイト（moguls-results）で審判点まで照合済みのデータを流用したとき、その参照先
                   'upstream': cls.get('upstream')},
        'verification': {},
    }
    runs = [make_run(rec, rnd, rules) for rec in records]
    return rnd, runs


def make_run(rec, rnd, rules):
    suffix = '' if rec.get('counting', True) else '-Q1ref'
    run = {
        'run_id': f"{rnd['round_id']}-{rec['athlete_id']}{suffix}",
        'round_id': rnd['round_id'], 'event_id': rnd['event_id'], 'season': rnd['season'], 'series': rnd['series'],
        'discipline': rnd['discipline'], 'tier': rnd['tier'],
        'gender': rnd['gender'], 'round': rnd['round'], 'date': rnd['date'],
        'rank': rec.get('rank'), 'bib': rec.get('bib'), 'athlete_id': rec['athlete_id'],
        'fis_code': rec.get('fis_code'), 'saj_no': rec.get('saj_no'),
        'name': rec['name'], 'noc': rec.get('noc'), 'yb': rec.get('yb'),
        'affiliation': rec.get('affiliation'), 'club': rec.get('club'),
        'status': rec['status'], 'reserve_judge': bool(rec.get('reserve_judge')),
        'seconds': None, 'time_points': None, 'air': [], 'air_total': None,
        'base': [], 'base_discard': [], 'base_total': None,
        'ded': [], 'ded_discard': [], 'ded_total': None,
        'turns_total': None, 'turns_floor_applied': False, 'run_score': None, 'tie': rec.get('tie'),
        'q_block': rec.get('q_block'), 'counting': bool(rec.get('counting', True)), 'best_score': rec.get('best_score'),
        'components': None,
        'provenance': {'pdf': rnd['source']['pdf'], 'page': rec.get('page'),
                       'parser_version': rnd['source']['parser_version'], 'rules_version': rnd['source']['rules_version']},
        # printed values kept for verification (removed before publishing)
        '_printed': {k: rec.get(k) for k in ('time_points', 'air_total', 'base_total', 'ded_total', 'turns_total', 'run_score')},
        '_exceptions': rec.get('exceptions') or {},
    }
    if rec['status'] != 'OK':
        return run
    if rnd['tier'] != 'detail':
        # score tier: keep printed totals as they are, nothing to recompute
        run['seconds'] = rec.get('seconds')
        for k in ('time_points', 'air_total', 'turns_total', 'run_score'):
            run[k] = rec.get(k)
        return run
    rc = scoring.recompute(rec, rnd['pace_time'], rules)
    # 規則ファイルに根拠つきで登録された例外（印字を正とする）: その要素だけ印字値に置き換え、合計を取り直す。
    # 置き換えた要素は layer2 が警告として報告する（黙って通さない）。
    rc['_exception_applied'] = {}
    for fld, exc in run['_exceptions'].items():
        if fld in rc and rc[fld] is not None and run['_printed'].get(fld) is not None \
                and _num_eq(exc.get('calc'), rc[fld]) and _num_eq(exc.get('printed'), run['_printed'][fld]):
            rc['_exception_applied'][fld] = (rc[fld], exc)
            rc[fld] = Decimal(str(run['_printed'][fld]))
    if rc['_exception_applied']:
        tp = rc['time_points'] if rc['time_points'] is not None else Decimal(str(rec['time_points']))
        rc['run_score'] = tp + rc['air_total'] + rc['turns_total']
    run['seconds'] = rec['seconds']
    run['time_points'] = f2(rc['time_points']) if rc['time_points'] is not None else rec['time_points']
    run['air'] = [{'J6': j['J6'], 'J7': j['J7'], 'jump': j['jump'], 'dd': j['DD'],
                   'v6': f2(p[0]), 'v7': f2(p[1]), 'jump_score': float(p[2])}
                  for j, p in zip(rec['air_jumps'], rc['air_parts'])]
    run['air_total'] = f2(rc['air_total'])
    run['base'] = rec['base_scores']; run['base_discard'] = rc['base_discard']; run['base_total'] = f2(rc['base_total'])
    run['ded'] = rec['ded_scores']; run['ded_discard'] = rc['ded_discard']; run['ded_total'] = f2(rc['ded_total'])
    run['turns_total'] = f2(rc['turns_total']); run['turns_floor_applied'] = rc['turns_floor_applied']
    run['run_score'] = f2(rc['run_score'])
    run['_recomputed'] = rc
    return run
