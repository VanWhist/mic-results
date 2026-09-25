"""ブラウザで写した SAJ 順位表 JSON（受信機の l5_*.json）を etl/layer5_cache/saj/<key>.json に振り分ける。

    python -m etl.tests.l5_import <l5_*.json ...>

各要素: {key, url, status, rows}。rows が空（表なし）でもファイルを作る（第5層は upstream_missing にする）。
"""
import json, os, sys, datetime
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, '..', 'layer5_cache', 'saj')


def main(paths):
    os.makedirs(CACHE, exist_ok=True)
    n = 0
    empty = 0
    for p in paths:
        raw = open(p, 'rb').read()
        if raw.startswith(b'b='):
            raw = raw[2:]
        items = json.loads(raw.decode('utf-8').strip())
        for it in items:
            if not it.get('key'):
                continue
            rows = it.get('rows') or []
            if not rows:
                empty += 1
            out = {'url': 'https://sajdb.shikuminet.jp' + it['url'] if it['url'].startswith('/') else it['url'],
                   'status': it.get('status'), 'rows': rows, 'fetched_at': datetime.date.today().isoformat()}
            with open(os.path.join(CACHE, it['key'] + '.json'), 'w', encoding='utf-8') as fh:
                json.dump(out, fh, ensure_ascii=False, indent=0)
            n += 1
    print(f"{n} ページを保存（表なし {empty}）→ {os.path.abspath(CACHE)}")


if __name__ == '__main__':
    main(sys.argv[1:])
