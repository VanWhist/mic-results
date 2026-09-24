"""moguls-results の公開データから registry・正解データ・第5層の結果・別名を同期する。

    python -m etl.adapters.moguls_results.sync_registry

生成・更新するもの:
  etl/registry/moguls_results.json   大会1件＝registry 1件（event_id は moguls-results と同じにして相互リンクできるようにする）
  golden/golden_moguls_results.json  moguls-results の正解データのうち得点段階で持つ項目だけ
  etl/layer5_status.json             moguls-results が FIS 公式 Web と照合した結果（ok / upstream_missing）をラウンド単位で引き継ぐ
  etl/athlete_aliases.json           moguls-results で確認済みの読み・漢字表記を、未登録の FIS コードにだけ足す
moguls-results 側でデータが更新されたら再実行する。手で書き換えない。
"""
import json, os, sys
from ... import config
from . import adapter, upstream_golden_keys

sys.stdout.reconfigure(encoding='utf-8')


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def dump_json(path, obj, indent=1):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
        fh.write('\n')


def main():
    up = adapter.upstream()
    formats = up['rules']['formats']
    events = []
    for e in sorted(up['events'].values(), key=lambda e: (e['date_from'] or '', e['event_id'])):
        rounds = e['rounds']
        rules_versions = sorted({r['source'].get('rules_version') for r in rounds if r['source'].get('rules_version')})
        fmt = formats.get(e['format'], {})
        pdfs = []
        for r in rounds:
            src = r['source']
            same = next((p for p in pdfs if p['sha256'] == src['pdf_sha256']), None)
            if same:  # 予選1・予選2 が1つの PDF に載る様式（Q1/Q2 二段）は1件にまとめる
                same['round_ids'].append(r['round_id'])
                same['url'] = same['url'] or src.get('fis_url')
                continue
            pdfs.append({'path': 'moguls-results/' + src['pdf'], 'sha256': src['pdf_sha256'], 'url': src.get('fis_url'),
                         'page_url': adapter.event_url(e['event_id']), 'round_ids': [r['round_id']], 'saved_at': None})
        events.append({
            'event_id': e['event_id'], 'season': e['season'], 'series': e['series'], 'grade': None, 'discipline': 'MO',
            'name_ja': None, 'nation': e.get('nation'), 'tier': 'score', 'adapter': 'moguls_results',
            'pdfs': pdfs, 'rules': rules_versions[-1] if rules_versions else None,
            'format': {'label': fmt.get('label') or e.get('format_label'), 'upstream_format': e['format'],
                       'rounds': fmt.get('rounds'), 'advance': fmt.get('advance', {})},
            'known_gaps': e.get('known_gaps') or {},
            'notes': f"moguls-results dataVersion {up['manifest']['dataVersion']} から同期",
        })
    reg_path = os.path.join(config.REGISTRY_DIR, 'moguls_results.json')
    dump_json(reg_path, {'_comment': 'sync_registry.py が moguls-results の公開データから生成する。手で編集しない。',
                         'synced_from': {'repo': config.MOGULS_RESULTS_REPO, 'dataVersion': up['manifest']['dataVersion'],
                                         'builtAt': up['manifest']['builtAt']},
                         'events': events})
    print(f"registry: {len(events)} 大会 / {sum(len(e['pdfs']) for e in events)} ラウンド → {reg_path}")

    # 正解データ（得点段階の項目だけ）
    gold_dir = os.path.join(config.MOGULS_RESULTS_REPO, 'golden')
    runs = []
    for fn in sorted(os.listdir(gold_dir)) if os.path.isdir(gold_dir) else []:
        if not fn.endswith('.json'):
            continue
        g = load_json(os.path.join(gold_dir, fn), {})
        for item in g.get('runs', []):
            runs.append({k: v for k, v in item.items() if k in upstream_golden_keys.SCORE_KEYS})
    gold_out = os.path.join(config.GOLDEN_DIR, 'golden_moguls_results.json')
    dump_json(gold_out, {'_comment': 'moguls-results/golden/*.json（目視で作った正解）から、得点段階で持つ項目だけを写したもの。sync_registry.py が生成。',
                         'runs': runs})
    print(f"golden: {len(runs)} 本 → {gold_out}")

    # 第5層（FIS 公式 Web との照合）の結果を引き継ぐ
    l5 = load_json(config.LAYER5_CACHE, {})
    n5 = 0
    for e in up['events'].values():
        for r in e['rounds']:
            st = (r.get('verification') or {}).get('layer5')
            if st in ('ok', 'upstream_missing'):
                l5[r['round_id']] = st
                n5 += 1
    dump_json(config.LAYER5_CACHE, l5, indent=0)
    print(f"layer5: {n5} ラウンドの結果を引き継ぎ → {config.LAYER5_CACHE}")

    # 別名（読み・漢字）: 未登録の FIS コードだけ足す
    al = load_json(config.ATHLETE_ALIASES, {})
    up_al = load_json(os.path.join(config.MOGULS_RESULTS_REPO, 'etl', 'athlete_aliases.json'), {})
    n_al = 0
    for code, v in up_al.items():
        if code.startswith('_') or code in al:
            continue
        al[code] = {k: v[k] for k in ('kana', 'kanji') if v.get(k)}
        al[code]['source'] = 'moguls-results athlete_aliases.json'
        n_al += 1
    dump_json(config.ATHLETE_ALIASES, al)
    print(f"aliases: {n_al} 件追加 → {config.ATHLETE_ALIASES}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
