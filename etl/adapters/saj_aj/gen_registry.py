"""SAJ 競技データバンクから保存した PDF（inventory/saj_pdf_plan.json の計画どおりに置いたもの）から registry を生成する。

    python -m etl.adapters.saj_aj.gen_registry

- MO → etl/registry/saj_db.json（adapter saj_aj、tier detail）
- DM → etl/registry/saj_db_dm.json（adapter saj_dm、tier rank）
- 全日本（NC）のうち 2016-17〜2025-26 は etl/registry/saj_aj.json（札幌スキー連盟の PDF）に登録済みなので除外する。
- 1 大会 = SAJ の大会（competition）× レース（codex の下4桁。女子は男子＋5000）。同じ大会に第1戦・第2戦があれば別の event になる。
- 規則は季節ごとの汎用ファイル etl/rules/events/規則_SAJ_<season>.json（無ければ作る）。大会固有の例外は registry の
  pace_by_sheet / recompute_exceptions に書く（sheet 名は "<event_id>_<Q|F|SF>-<m|w>"）。
既存の registry にある event は sha256 と手で書いた項目（rules・format・pace_by_sheet・recompute_exceptions・notes）を保持する。
"""
import json, os, sys, collections, datetime
from ... import config
from ...normalize import sha256_file

sys.stdout.reconfigure(encoding='utf-8')
PLAN = os.path.join(config.REPO, 'inventory', 'saj_pdf_plan.json')
SAJ_BASE = 'https://sajdb.shikuminet.jp'
GENERIC_RULES = {
    "turns": {"5": {"trim": True, "floor": "0.3"}, "3": {"trim": False, "floor": "0.3"}},
    "air": {"cap": "10"}, "time": {"a": "48", "b": "32", "max": "20"},
}


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def dump_json(path, obj):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
        fh.write('\n')


def ensure_rules(season):
    name = f"規則_SAJ_{season}"
    path = os.path.join(config.EVENT_RULES_DIR, name + '.json')
    if not os.path.exists(path):
        dump_json(path, {"season": season, "basis": f"SAJ 公認大会の汎用規則（{season}）。全日本の規則ファイルと同じ式。大会ごとの例外は registry 側に書く",
                         **GENERIC_RULES})
    return name


def main():
    plan = load_json(PLAN, [])
    registered_aj = {(e['season']) for e in load_json(os.path.join(config.REGISTRY_DIR, 'saj_aj.json'), {}).get('events', [])}
    groups = collections.defaultdict(list)
    n_missing = 0
    for it in plan:
        path = os.path.join(config.PDF_ROOT, it['rel'])
        if not os.path.exists(path):
            n_missing += 1
            continue
        if it['series'] == 'SAJ_AJ' and it['season'] in registered_aj:
            continue
        race = int(it['codex']) % 5000
        key = (it['discipline'], it['series'], it['season'], it['comp'], race)
        groups[key].append(it)
    out = {'MO': [], 'DM': []}
    for key, items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[1][0]['date'], kv[0][3], kv[0][4])):
        disc, series, season, comp, race = key
        items.sort(key=lambda it: it['gender'])
        first = items[0]
        event_id = f"{season}-{series.lower()}-{comp}-{race:04d}"
        pdfs = []
        for it in items:
            path = os.path.join(config.PDF_ROOT, it['rel'])
            pdfs.append({'path': it['rel'], 'sha256': sha256_file(path), 'url': it['src'],
                         'page_url': SAJ_BASE + it['result_data'] if it.get('result_data') else None,
                         'gender': it['gender'], 'codex': it['codex'], 'saved_at': datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat(),
                         'src_name': it['src_name']})
        ev = {
            'event_id': event_id, 'season': season, 'series': series, 'grade': first['grade'], 'discipline': disc,
            'name_ja': first['comp_name'], 'date': first['date'].replace('/', '-'), 'saj_comp': comp, 'saj_category': first['category'],
            'tier': 'detail' if disc == 'MO' else 'rank', 'adapter': 'saj_aj' if disc == 'MO' else 'saj_dm',
            'pdfs': pdfs, 'rules': ensure_rules(season) if disc == 'MO' else None, 'sheet_prefix': event_id,
            'format': {'label': None, 'advance': {}}, 'notes': '',
        }
        out[disc].append(ev)
    for disc, fn in (('MO', 'saj_db.json'), ('DM', 'saj_db_dm.json')):
        path = os.path.join(config.REGISTRY_DIR, fn)
        old = {e['event_id']: e for e in load_json(path, {}).get('events', [])}
        for ev in out[disc]:
            o = old.get(ev['event_id'])
            if o:
                for k in ('rules', 'format', 'pace_by_sheet', 'recompute_exceptions', 'notes', 'tier', 'grade', 'name_ja', 'skip'):
                    if k in o:
                        ev[k] = o[k]
        dump_json(path, {'_comment': f'gen_registry.py が inventory/saj_pdf_plan.json と保存済み PDF から生成。rules・format・pace_by_sheet・recompute_exceptions・notes・skip は手で編集してよい（再生成しても保持される）',
                         'generated_at': datetime.datetime.now().isoformat(timespec='seconds'), 'events': out[disc]})
        print(f"{fn}: {len(out[disc])} 大会 / {sum(len(e['pdfs']) for e in out[disc])} PDF")
    print(f"未保存の PDF: {n_missing}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
