"""W杯・世界選手権・五輪のデュアルモーグルの最終成績（全試合のリザルトに保存した PDF）から registry を生成する。

    python -m etl.adapters.fis_dm_pdf.gen_registry_wc

- 入力: MOGULS_PDF_ROOT の '<シーズン または 世界選手権・オリンピック>/<会場>/<会場>_<男子|女子>デュアルモーグル決勝_<codex>.pdf'
  と inventory/fis_calendar_events.json（FIS の大会番号）と etl/registry/moguls_results.json（同じ大会のモーグル）。
- 出力: etl/registry/moguls_results_dm.json（adapter fis_dm_pdf、tier rank = 順位のみ。城さんの判断 ①、2026-11-07）。
- 1 フォルダ = 1 大会。同じフォルダのモーグルの event_id を meet_event に持ち、大会一覧で 1 枚のカードにまとめる。
既存の registry にある event は、手で書いた項目（PRESERVE）を保持する。
"""
import json, os, re, sys, unicodedata
from ... import config
from ...normalize import sha256_file
from .adapter import parse_pdf

sys.stdout.reconfigure(encoding='utf-8')
CALENDAR = os.path.join(config.REPO, 'inventory', 'fis_calendar_events.json')
MO_REGISTRY = os.path.join(config.REGISTRY_DIR, 'moguls_results.json')
OUT = os.path.join(config.REGISTRY_DIR, 'moguls_results_dm.json')
PRESERVE = ('notes', 'name_ja', 'skip', 'exclude_pdfs', 'competitor_count_exceptions')
EVENT_URL = 'https://www.fis-ski.com/DB/general/event-details.html?sectorcode=FS&eventid={}&seasoncode={}'
RE_FILE = re.compile(r'^(.+)_(男子|女子)デュアルモーグル決勝_(\d+)\.pdf$')
SERIES_BY_FOLDER = {'世界選手権': 'WSC', 'オリンピック': 'OWG'}


def fold(s):
    """会場名の照合用（'Idre Fjäll' と 'Idre Fjall'）: アクセントと記号を落として小文字に"""
    s = unicodedata.normalize('NFKD', s)
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in s if not unicodedata.combining(c)).lower())


def season_of(date):
    """大会の日付（'2023-02-26'）→ シーズン（'2022-23'）"""
    y, m = int(date[:4]), int(date[5:7])
    start = y if m >= 7 else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def main():
    calendar = json.load(open(CALENDAR, encoding='utf-8'))
    mo_events = json.load(open(MO_REGISTRY, encoding='utf-8'))['events']
    mo_by_folder = {}
    for e in mo_events:
        for p in e['pdfs']:
            mo_by_folder.setdefault(os.path.dirname(p['path'][len('moguls-results/'):]), set()).add(e['event_id'])
    old = {e['event_id']: e for e in json.load(open(OUT, encoding='utf-8')).get('events', [])} if os.path.exists(OUT) else {}
    folders = {}
    for root, _, names in os.walk(config.MOGULS_PDF_ROOT):
        for n in names:
            m = RE_FILE.match(n)
            if m:
                folders.setdefault(os.path.relpath(root, config.MOGULS_PDF_ROOT).replace('\\', '/'), []).append((n, m))
    out = []
    for folder, files in sorted(folders.items()):
        top = folder.split('/')[0]
        series = SERIES_BY_FOLDER.get(top, 'WC')
        pdfs = []
        for n, m in sorted(files, key=lambda x: (x[1].group(2) != '男子', int(x[1].group(3)))):
            rel = f"{folder}/{n}"
            path = os.path.join(config.MOGULS_PDF_ROOT, rel)
            meta, athletes, _ = parse_pdf(path)
            if not athletes:
                sys.exit(f"{rel}: 最終成績の表が読めない")
            g = 'M' if m.group(2) == '男子' else 'W'
            pdfs.append({'path': 'moguls-results/' + rel, 'sha256': sha256_file(path), 'url': None, 'page_url': None,
                         'gender': g, 'codex': m.group(3), 'src_type': 'RLF', '_date': meta['date']})
        season = season_of(min(p['_date'] for p in pdfs))
        season_end = int(season[:4]) + 1
        place_key = fold(files[0][1].group(1))
        cal = [e for e in calendar if e['season_code'] == season_end and series in e['categories']
               and 'DM' in (e['disciplines'] or '') and fold(e['place']) == place_key]
        if len(cal) != 1:
            sys.exit(f"{folder}: FIS の日程で大会を 1 つに決められない（{len(cal)} 件）")
        mo = mo_by_folder.get(folder) or set()
        if len(mo) != 1:
            sys.exit(f"{folder}: 同じフォルダのモーグルの大会を 1 つに決められない（{sorted(mo)}）")
        fis_ev, place = cal[0]['event_id'], cal[0]['place']
        event_url = EVENT_URL.format(fis_ev, season_end)
        for p in pdfs:
            p['page_url'] = event_url
            del p['_date']
        rep = pdfs[0]['codex']  # 男子の CODEX（fis_overseas_dm.json と同じ）
        event_id = f"{season}-{series.lower()}-{fold(place)}-{rep}"
        ev = {'event_id': event_id, 'season': season, 'series': series, 'grade': None, 'discipline': 'DM',
              'name_ja': None, 'place': place, 'fis_event_id': fis_ev, 'meet_event': next(iter(mo)), 'page_url': event_url,
              'tier': 'rank', 'adapter': 'fis_dm_pdf', 'rules': None, 'pdfs': pdfs,
              'format': {'label': None, 'advance': {}}, 'notes': ''}
        for k in PRESERVE:
            if k in old.get(event_id, {}):
                ev[k] = old[event_id][k]
        out.append(ev)
    out.sort(key=lambda e: e['event_id'])
    with open(OUT, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump({'_about': 'W杯・世界選手権・五輪のデュアルモーグル最終成績（etl/adapters/fis_dm_pdf/gen_registry_wc.py が生成。手書き項目は保持）',
                   'events': out}, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    print(f"登録 {len(out)} 大会 / {sum(len(e['pdfs']) for e in out)} PDF → {os.path.relpath(OUT, config.REPO)}")


if __name__ == '__main__':
    main()
