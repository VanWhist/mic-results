"""fis_pdf アダプタの動作確認: moguls-results と同じ FIS 様式の PDF（W杯 2025-26 Nanto-Toyama 男子）を
registry 相当の dict で読み、第0〜4層まで通す。本番の registry には入れない（W杯は moguls_results 経由）。

    python -m etl.tests.test_fis_adapter
"""
import os, sys, collections
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from etl import config, verify, normalize
from etl.adapters.fis_pdf import adapter

VENUE = os.path.join(config.MOGULS_PDF_ROOT, '2025-26シーズン', 'Nanto-Toyama')
EV = {
    'event_id': 'test-fis-nanto', 'season': '2025-26', 'series': 'WC', 'grade': None, 'discipline': 'MO',
    'name_ja': 'テスト', 'tier': 'detail', 'adapter': 'fis_pdf', 'rules': '2025-26',
    'format': {'advance': {'Q': {'to': 'F1', 'n': 16}, 'F1': {'to': 'F2', 'n': 6}}},
    'pdfs': [
        {'path': os.path.join(VENUE, 'Nanto-Toyama_男子モーグル予選_8213.pdf'), 'round': 'Q', 'gender': 'M', 'codex': '8213'},
        {'path': os.path.join(VENUE, 'Nanto-Toyama_男子モーグル決勝1_8213.pdf'), 'round': 'F1', 'gender': 'M', 'codex': '8213'},
        {'path': os.path.join(VENUE, 'Nanto-Toyama_男子モーグル決勝2_8213.pdf'), 'round': 'F2', 'gender': 'M', 'codex': '8213'},
    ],
}


def main():
    ctxs = adapter.load_event(EV, 'test')
    findings = []
    rounds_ctx = []
    dd_seen = collections.defaultdict(set)
    for c in ctxs:
        assert not c.get('error_only'), c
        rnd, runs = normalize.make_round(c['cls'], c['meta'], c['records'], c['rules'], 'test')
        findings += c['findings']
        findings += verify.layer2(rnd['round_id'], runs, None, rnd['gender'], dd_seen)
        findings += verify.layer3_rank(rnd['round_id'], runs, c['rules'])
        rounds_ctx.append({'cls': c['cls'], 'round': rnd, 'runs': runs, 'records_a': c['records'], 'rules': c['rules'], 'ab_compared': c['ab_compared']})
    findings += verify.layer0(rounds_ctx, {}, True, check_missing=False)
    rbc = {x['round']['round']: (x['round'], x['runs']) for x in rounds_ctx}
    findings += verify.layer3_progression('test-fis-nanto', rbc, EV['format']['advance'])
    findings += verify.layer4([r for x in rounds_ctx for r in x['runs']], [x['round'] for x in rounds_ctx])
    errs = [f for f in findings if f.level == 'error']
    for f in findings:
        print(f"  [{f.level}] {f.layer} {f.round_id}: {f.message}")
    print(f"ラウンド {len(rounds_ctx)} / 記録 {sum(len(x['runs']) for x in rounds_ctx)} / エラー {len(errs)} / A/B 比較 {sum(1 for x in rounds_ctx if x['ab_compared'])}")
    return 1 if errs else 0


if __name__ == '__main__':
    sys.exit(main())
