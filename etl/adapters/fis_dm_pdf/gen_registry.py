"""FIS サイトから保存した海外大会（EC・NAC・ANC・WJC・UVS）のデュアルモーグルの最終成績から registry を生成する。

    python -m etl.adapters.fis_dm_pdf.gen_registry

- 入力: inventory/fis_pdf_manifest.csv（保存した PDF と置き場所）と inventory/fis_overseas_survey.jsonl（大会ページの確認記録）。
- 出力: etl/registry/fis_overseas_dm.json（adapter fis_dm_pdf、tier rank = 順位のみ）。
- 対象は最終成績の報告書（RLF。2016-17 以降の FIS 標準の様式）で、アダプタが読める（選手の行がある）もの。
  2015-16 以前の様式、EC 2017 Albiez（仏式）、NAC 2022 Apex（文字表）は対象外（城さんの判断、2026-09-29）。
- 1 大会 = FIS の event × 同じ日の男女（codex が隣り合う組）。モーグル（fis_overseas.json）とは別の大会として登録する。
既存の registry にある event は、手で書いた項目（PRESERVE）を保持する。
"""
import csv, json, os, sys, collections
from ... import config
from ...normalize import sha256_file
from .adapter import parse_pdf

sys.stdout.reconfigure(encoding='utf-8')
INV = os.path.join(config.REPO, 'inventory')
MANIFEST = os.path.join(INV, 'fis_pdf_manifest.csv')
SURVEY = os.path.join(INV, 'fis_overseas_survey.jsonl')
OUT = os.path.join(config.REGISTRY_DIR, 'fis_overseas_dm.json')
PRESERVE = ('notes', 'name_ja', 'skip', 'exclude_pdfs', 'competitor_count_exceptions')
RACE_URL = 'https://www.fis-ski.com/DB/general/results.html?sectorcode=FS&raceid={}'


def main():
    rows = list(csv.DictReader(open(MANIFEST, encoding='utf-8-sig')))
    events = {}
    for line in open(SURVEY, encoding='utf-8'):
        ev = json.loads(line)
        if not ev.get('supplement'):
            events[ev['event_id']] = ev
    old = {e['event_id']: e for e in json.load(open(OUT, encoding='utf-8')).get('events', [])} if os.path.exists(OUT) else {}
    races = collections.defaultdict(list)
    for r in rows:
        name = os.path.basename(r['dest'])[:-4]
        pre, cat, place, codex, g, disc, typ = name.split('_')
        if disc != 'DM' or typ != 'RLF':
            continue
        races[r['event']].append(dict(rel=r['dest'].replace('\\', '/'), g=g, codex=codex, raceid=r['raceid'],
                                      season_end=int(pre[3:]), cat=cat, place=place))
    out, skipped = [], collections.Counter()
    for fis_ev, files in sorted(races.items()):
        files.sort(key=lambda x: int(x['codex']))
        groups, used = [], set()
        for f in files:
            if f['codex'] in used:
                continue
            mate = next((x for x in files if x['codex'] not in used and x['codex'] != f['codex'] and x['g'] != f['g']
                         and abs(int(x['codex']) - int(f['codex'])) <= 2), None)
            grp = [f] + ([mate] if mate else [])
            used.update(x['codex'] for x in grp)
            groups.append(grp)
        sv = events.get(fis_ev, {})
        for grp in groups:
            f0 = grp[0]
            season = f"{f0['season_end'] - 1}-{str(f0['season_end'])[2:]}"
            if f0['season_end'] < 2017:
                skipped['2015-16 以前'] += 1
                continue
            pdfs = []
            for x in sorted(grp, key=lambda x: (x['g'] != 'M', int(x['codex']))):
                path = os.path.join(config.PDF_ROOT, x['rel'])
                _, athletes, _ = parse_pdf(path)
                if not athletes:
                    skipped[f"読めない様式 {os.path.basename(x['rel'])}"] += 1
                    continue
                pdfs.append({'path': x['rel'], 'sha256': sha256_file(path), 'url': None, 'page_url': RACE_URL.format(x['raceid']),
                             'gender': x['g'], 'codex': x['codex'], 'src_type': 'RLF'})
            if not pdfs:
                continue
            rep = min(grp, key=lambda x: (x['g'] != 'M', int(x['codex'])))['codex']
            event_id = f"{season}-{f0['cat'].lower()}-{f0['place'].lower()}-{rep}"
            ev = {'event_id': event_id, 'season': season, 'series': f0['cat'], 'grade': None, 'discipline': 'DM',
                  'name_ja': None, 'place': sv.get('place') or f0['place'], 'fis_event_id': fis_ev,
                  'page_url': f"https://www.fis-ski.com/DB/general/event-details.html?sectorcode=FS&eventid={fis_ev}&seasoncode={f0['season_end']}",
                  'tier': 'rank', 'adapter': 'fis_dm_pdf', 'rules': None, 'pdfs': pdfs,
                  'format': {'label': None, 'advance': {}}, 'notes': ''}
            for k in PRESERVE:
                if k in old.get(event_id, {}):
                    ev[k] = old[event_id][k]
            out.append(ev)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump({'_about': 'FIS サイトから保存した海外大会のデュアルモーグル最終成績（etl/adapters/fis_dm_pdf/gen_registry.py が生成。手書き項目は保持）',
                   'events': out}, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    print(f"登録 {len(out)} 大会 / {sum(len(e['pdfs']) for e in out)} PDF → {os.path.relpath(OUT, config.REPO)}")
    for k, v in skipped.most_common():
        print(f"  見送り {v}: {k}")


if __name__ == '__main__':
    main()
