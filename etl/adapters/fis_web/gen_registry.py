"""FIS 公式サイトの結果ページから保存した海外 FIS 格・Open レース（結果の PDF が無いもの）の registry を生成する。

    python -m etl.adapters.fis_web.gen_registry

- 対象: 日本人が出たのに FIS サイトに結果の PDF が無い 2014-15 以降のレース（城さんの判断 ①、2026-10-08）。
  順位のみの段階（tier rank）で載せ、ページの得点（Result）は参考として表示する。
- 入力: PDF_ROOT/FIS_WEB/raw/<raceid>.json（結果ページの表を Claude in Chrome で 1 ページずつ取り出したもの。
  PDF_ROOT/FIS_WEB/page_checks.txt にページ上で計算した照合値）と inventory/fis_overseas_survey.jsonl（2026-10-04 の調査。
  レースごとの出場人数と日本人の行を、結果ページから別の読み方で記録したもの）。
- 出力: etl/registry/fis_web.json（adapter fis_web）。
- 1 大会 = FIS の event × 同じ日の男女（fis_pdf の gen_registry と同じ。CODEX が 2 以内の男女を組にし、男子の CODEX を代表にした event_id）。
既存の registry にある event は、手で書いた項目（PRESERVE）を保持する。
"""
import datetime, hashlib, json, os, re, sys, collections
from ... import config
from ...normalize import sha256_file

sys.stdout.reconfigure(encoding='utf-8')
WEB_DIR = os.path.join(config.PDF_ROOT, 'FIS_WEB')
RAW_DIR = os.path.join(WEB_DIR, 'raw')
CHECKS = os.path.join(WEB_DIR, 'page_checks.txt')
SURVEY = os.path.join(config.REPO, 'inventory', 'fis_overseas_survey.jsonl')
OUT = os.path.join(config.REGISTRY_DIR, 'fis_web.json')
PRESERVE = ('notes', 'tier', 'name_ja', 'skip', 'format')
RACE_URL = 'https://www.fis-ski.com/DB/general/results.html?sectorcode=FS&raceid={}'
EVENT_URL = 'https://www.fis-ski.com/DB/general/event-details.html?sectorcode=FS&eventid={}&seasoncode={}'
NOTE = ('FIS サイトに結果の PDF が無いレース。FIS 公式サイトの結果ページ（順位・FIS コード・氏名・国・生年・得点・FIS ポイント）から'
        '取り込み、順位のみの段階で載せる。得点は参考（審判の点からの検算はしていない）（城さんの判断 ①、2026-10-08）')


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def page_date(head):
    """結果ページの見出しの日付（'December 17, 2016'）→ ISO"""
    for s in head:
        try:
            return datetime.datetime.strptime(s, '%B %d, %Y').date().isoformat()
        except ValueError:
            continue
    return None


def main():
    checks = dict(l.split() for l in open(CHECKS, encoding='utf-8') if l.strip())
    survey = {}
    for line in open(SURVEY, encoding='utf-8'):
        ev = json.loads(line)
        for r in ev['races']:
            survey[r['raceid']] = (ev, r)
    pages = []
    for fn in sorted(os.listdir(RAW_DIR)):
        if not fn.endswith('.json'):
            continue
        path = os.path.join(RAW_DIR, fn)
        raw = open(path, 'rb').read().rstrip(b'\r\n')
        d = json.loads(raw)
        rid = d['raceid']
        chk = '-'.join('%03d' % x for x in hashlib.sha256(raw).digest()[:8])
        if checks.get(rid) != chk:
            sys.exit(f"{fn}: ページ上の照合値 {checks.get(rid)} と保存したファイル {chk} が違う")
        ev, race = survey[rid]
        gender = 'W' if d['head'][3].startswith('Women') else 'M' if d['head'][3].startswith('Men') else None
        cat = race['race'].rsplit('|', 1)[-1].strip()
        pages.append({'rid': rid, 'd': d, 'ev': ev, 'race': race, 'g': gender, 'codex': d['codex'], 'cat': cat,
                      'rel': f"FIS_WEB/raw/{fn}", 'path': path})
    old = {e['event_id']: e for e in load_json(OUT, {}).get('events', [])}
    out = []
    by_event = collections.defaultdict(list)
    for p in pages:
        by_event[p['ev']['event_id']].append(p)
    for fis_ev, items in sorted(by_event.items(), key=lambda kv: kv[0]):
        items.sort(key=lambda p: int(p['codex']))
        groups, used = [], set()
        for p in items:
            if p['rid'] in used:
                continue
            mate = next((q for q in items if q['rid'] not in used and q is not p and q['g'] != p['g']
                         and abs(int(q['codex']) - int(p['codex'])) <= 2), None)
            grp = [p] + ([mate] if mate else [])
            used.update(x['rid'] for x in grp)
            groups.append(grp)
        for grp in groups:
            p0 = grp[0]
            season_end = int(re.search(r'(\d{4})/(\d{4})', p0['d']['title']).group(2))
            season = f"{season_end - 1}-{str(season_end)[2:]}"
            rep = min(grp, key=lambda p: (p['g'] != 'M', int(p['codex'])))
            cats = {p['cat'] for p in grp}
            if len(cats) != 1:
                sys.exit(f"{fis_ev}: 1 大会の中で FIS・OPN が混ざる {cats}")
            place = re.sub(r'[^A-Za-z0-9]', '', p0['ev']['place'].split(',')[0])
            event_id = f"{season}-{cats.pop().lower()}-{place.lower()}-{rep['codex']}"
            ev = {
                'event_id': event_id, 'season': season, 'series': p0['cat'], 'grade': None, 'discipline': 'MO', 'name_ja': None,
                'place': p0['ev']['place'], 'fis_event_id': fis_ev, 'page_url': EVENT_URL.format(fis_ev, season_end),
                'tier': 'rank', 'adapter': 'fis_web',
                'pages': [{'path': p['rel'], 'sha256': sha256_file(p['path']), 'raceid': p['rid'], 'page_url': RACE_URL.format(p['rid']),
                           'gender': p['g'], 'codex': p['codex'], 'date': page_date(p['d']['head']), 'page_check': checks[p['rid']],
                           'survey': {'n': p['race']['n'], 'jpn': p['race']['jpn'], 'checked_at': p['ev'].get('checked_at')}}
                          for p in sorted(grp, key=lambda p: p['g'])],
                'format': {'label': None, 'advance': {}}, 'notes': NOTE,
            }
            o = old.get(event_id)
            if o:
                for k in PRESERVE:
                    if k in o:
                        ev[k] = o[k]
            out.append(ev)
    out.sort(key=lambda e: (e['season'], e['event_id']))
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'_comment': 'gen_registry.py（etl/adapters/fis_web）が FIS_WEB/raw と inventory/fis_overseas_survey.jsonl から生成。'
                               + '・'.join(PRESERVE) + ' は手で編集してよい（再生成しても保持される）',
                   'generated_at': datetime.datetime.now().isoformat(timespec='seconds'), 'events': out}, fh, ensure_ascii=False, indent=1)
    print(f"{OUT}: {len(out)} 大会 / {sum(len(e['pages']) for e in out)} レース")


if __name__ == '__main__':
    main()
