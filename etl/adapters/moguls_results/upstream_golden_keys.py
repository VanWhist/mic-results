"""moguls-results の正解データのうち、得点段階（score）の出力に存在する項目。審判ごとの点（base/ded/air）は写さない。"""
SCORE_KEYS = {'run_id', 'note', 'source', 'rank', 'bib', 'name', 'noc', 'yb', 'status', 'reserve_judge',
              'seconds', 'time_points', 'air_total', 'turns_total', 'run_score', 'tie'}
