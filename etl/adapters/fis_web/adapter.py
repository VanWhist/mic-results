"""fis_web アダプタ: FIS 公式サイトの結果ページから保存した海外 FIS 格・Open レース（結果の PDF が無いもの。順位のみ、tier rank）。

保存したページ（PDF_ROOT/FIS_WEB/raw/<raceid>.json）は、結果ページの表を Claude in Chrome で 1 ページずつ取り出したもの:
  {raceid, title, head（大会名・区分・種目・日付）, codex, cols（表頭）, rows（[状態の見出し, 表頭の順の欄…]）}
表頭は Rank / Bib / FIS code / Athlete / Year / Nation / Result / FIS Points（Bib・Result が無いページがある）。
状態の見出し（Did Not Start / Did Not Finish / Disqualified）の後の行は順位なし。
得点（Result）は審判の点からの検算ができないので run_score には入れず、components.web_result に参考として持つ（画面は「得点（参考）」）。

検証（どれもエラーなら大会ごと非公開）:
  - 2 回目の読み取りとの照合: 2026-10-04 の調査で同じページから別の読み方で記録した出場人数と日本人の行（順位・Bib・FIS コード・氏名）
  - 順位: 順位のある行が先頭から 1 で始まり昇順、状態の行に順位なし。FIS コード（7 桁）の重複なし
  - FIS ポイント: 順位が下がるほど減る（同順位は同じ値）
  - 得点: 決勝・予選などのまとまりごとに高い順（下がらない所で区切ったまとまりが 3 を超えたら警告）
"""
import json, os
from ... import config
from ...verify import Finding

PARSER_VERSION = 'FIS-WEB-1.0'
STATUS = {'Did Not Start': 'DNS', 'Did Not Finish': 'DNF', 'Disqualified': 'DSQ', 'Did not start': 'DNS', 'Did not finish': 'DNF'}
COLS = ('Rank', 'Bib', 'FIS code', 'Athlete', 'Year', 'Nation', 'Result', 'FIS Points')


def read_page(path):
    with open(path, encoding='utf-8') as fh:
        d = json.load(fh)
    cols = [c for c in d['cols'] if c in COLS]
    unknown = [c for c in d['cols'] if c not in COLS and c not in STATUS]
    rows, problems = [], []
    for i, row in enumerate(d['rows'], 1):
        st, cells = row[0], row[1:]
        if len(cells) != len(cols):
            problems.append(f"{i} 行目の欄の数 {len(cells)} が表頭 {len(cols)} と違う")
            continue
        r = dict(zip(cols, cells))
        r['status'] = STATUS.get(st, 'OK' if not st else st)
        rows.append(r)
    if unknown:
        problems.append(f"知らない表頭 {unknown}")
    return d, rows, problems


def survey_rows(jpn):
    """調査の日本人の行（'2 97 2537016 OKADA Takuma'・'1 - 2532040 YANAGIMOTO Rino'・'DNS 1 2532040 …'）→ (順位か状態, Bib, FIS コード, 氏名)"""
    out = []
    for s in jpn:
        t = s.split(' ', 3)
        out.append((t[0], None if t[1] == '-' else t[1], t[2], t[3]))
    return out


def check_round(round_id, rows, survey):
    f = []
    ok = [r for r in rows if r['status'] == 'OK']
    # 2 回目の読み取り（2026-10-04 の調査）との照合
    if survey:
        if survey.get('n') != len(rows):
            f.append(Finding('error', round_id, 'layer0', f"出場人数 {len(rows)} が調査の {survey.get('n')} と違う"))
        mine = {(r['Rank'] if r['status'] == 'OK' else r['status'], r.get('Bib') or None, r['FIS code'], r['Athlete'])
                for r in rows if r['Nation'] == 'JPN'}
        theirs = set(survey_rows(survey.get('jpn') or []))
        for x in sorted(mine - theirs):
            f.append(Finding('error', round_id, 'layer0', f"日本人の行 {x} が調査の記録に無い"))
        for x in sorted(theirs - mine):
            f.append(Finding('error', round_id, 'layer0', f"調査の日本人の行 {x} がページに無い"))
    else:
        f.append(Finding('error', round_id, 'layer0', '2 回目の読み取り（調査）の記録が registry に無い'))
    # 順位
    ranks = [int(r['Rank']) for r in ok if r.get('Rank', '').isdigit()]
    if len(ranks) != len(ok) or not ranks or ranks[0] != 1 or ranks != sorted(ranks):
        f.append(Finding('error', round_id, 'layer0', f"順位が 1 から昇順でない（{ranks[:5]}…）"))
    if any(r.get('Rank') for r in rows if r['status'] != 'OK'):
        f.append(Finding('error', round_id, 'layer0', '途中棄権・出走なしの行に順位がある'))
    codes = [r['FIS code'] for r in rows]
    if len(set(codes)) != len(codes) or not all(len(c) == 7 and c.isdigit() for c in codes):
        f.append(Finding('error', round_id, 'layer0', 'FIS コードが 7 桁でない・重複している'))
    # FIS ポイント: 順位が下がるほど減る（同順位は同じ値）
    pts = [(int(r['Rank']), float(r['FIS Points'])) for r in ok if r.get('Rank', '').isdigit() and r.get('FIS Points')]
    for (ra, pa), (rb, pb) in zip(pts, pts[1:]):
        if (ra == rb and pa != pb) or (rb > ra and pb >= pa):
            f.append(Finding('error', round_id, 'layer0', f"FIS ポイントが順位と合わない（{ra} 位 {pa}・{rb} 位 {pb}）"))
            break
    # 得点: まとまりごとに高い順
    if 'Result' in (rows[0] if rows else {}):
        res = [float(r['Result']) for r in ok if r.get('Result')]
        groups = 1 + sum(1 for a, b in zip(res, res[1:]) if b > a)
        if res and groups > 3:
            f.append(Finding('warning', round_id, 'layer0', f"得点が高い順のまとまり {groups} 個（決勝・予選の数より多い）"))
    return f


def load_event(ev, imported_at, log=print):
    ctxs = []
    for pg in ev['pages']:
        path = os.path.join(config.PDF_ROOT, pg['path'])
        try:
            d, rows, problems = read_page(path)
        except Exception as e:  # noqa
            ctxs.append({'error_only': True, 'event_id': ev['event_id'], 'message': f"結果ページの読み取り例外 {pg['path']}: {e!r}"})
            continue
        g = pg['gender']
        round_id = f"{ev['event_id']}-{g}-F1"
        findings = [Finding('error', round_id, 'layer0', f"{pg['path']}: {p}") for p in problems]
        if d.get('codex') != pg.get('codex'):
            findings.append(Finding('error', round_id, 'layer0', f"ページの CODEX {d.get('codex')} が registry の {pg.get('codex')} と違う"))
        findings += check_round(round_id, rows, pg.get('survey'))
        has_result = any('Result' in r for r in rows)
        cls = {
            'event_id': ev['event_id'], 'season': ev['season'], 'series': ev['series'], 'grade': ev.get('grade'),
            'discipline': 'MO', 'gender': g, 'round': 'F1',
            'round_text': 'Final Result（FIS 公式サイトの最終順位）', 'codex': pg.get('codex'),
            'tier': 'rank', 'panel': None, 'rel': pg['path'], 'path': path, 'pdf_sha256': pg.get('sha256'),
            'url': pg.get('page_url'), 'page_url': pg.get('page_url'), 'pages': None, 'name_ja': ev.get('name_ja'),
            'format': ev.get('format'), 'rules_version': None,
        }
        rmeta = {'date': pg.get('date'), 'date_text': pg.get('date'), 'venue': ev.get('place'), 'judges': [],
                 'pace_time': None, 'num_competitors': len(rows), 'parser_version': PARSER_VERSION,
                 'title': d.get('title'), 'officials': []}
        records = []
        for r in rows:
            records.append({
                'rank': int(r['Rank']) if r['status'] == 'OK' and r.get('Rank', '').isdigit() else None,
                'bib': int(r['Bib']) if r.get('Bib', '').isdigit() else None, 'saj_no': None,
                'fis_code': r['FIS code'], 'athlete_id': r['FIS code'], 'name': r['Athlete'], 'noc': r['Nation'],
                'yb': int(r['Year']) if r.get('Year', '').isdigit() else None, 'affiliation': None, 'club': None,
                'status': r['status'], 'reserve_judge': False, 'counting': True, 'q_block': None, 'best_score': None,
                'seconds': None, 'time_points': None, 'air_jumps': [], 'air_total': None, 'base_scores': [], 'ded_scores': [],
                'base_total': None, 'ded_total': None, 'turns_total': None, 'run_score': None, 'tie': None, 'page': None,
                # 得点は参考（FIS 公式サイトの値。審判の点からの検算はしていない）。FIS ポイントは検証用
                'components': {'web_result': (r.get('Result') or None) if has_result else None, 'fis_points': r.get('FIS Points') or None,
                               'source': 'fis_web'},
            })
        ctxs.append({'cls': cls, 'meta': rmeta, 'records': records, 'findings': findings, 'ab_compared': False, 'rules': {}})
        log(f"  {round_id}: {len(records)} 名")
    return ctxs
