"""種目別順位の絞り込みで除いた EC・NAC の 73 大会（inventory/fis_screened_events.json）に日本人の出場が無いかを、
1 大会ずつ inventory/fis_screened_check.jsonl に記録する。Claude in Chrome で人の速度（1 ページずつ）で開いた大会ページ・
レース結果ページから写した内容を記録するだけで、このスクリプト自体は FIS にアクセスしない。
使い方: python inventory/fis_screened_add.py '<json>'
  json = {"event_id": "...", "races": [{"raceid": "...", "codex": "...", "race": "M MO", "n": 50,
          "jpn": ["rank NAME"]}], "note": "..."}
"""
import datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fis_screened_check.jsonl')
EVENTS = os.path.join(HERE, 'fis_screened_events.json')


def main():
    rec = json.loads(sys.argv[1])
    ev = {e['event_id']: e for e in json.load(open(EVENTS, encoding='utf-8'))}[rec['event_id']]
    rec = {'event_id': rec['event_id'], 'place': ev['place'], 'season': ev['season_code'], 'cat': ev['categories'][0], **rec}
    rec['checked_at'] = datetime.date.today().isoformat()
    rec['jpn_total'] = sum(len(r.get('jpn') or []) for r in rec.get('races', []))
    with open(OUT, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    done = sum(1 for _ in open(OUT, encoding='utf-8'))
    print(f"記録 {rec['event_id']} {ev['place']} {ev['season_code']}: 日本人 {rec['jpn_total']} 名（{done}/{len(json.load(open(EVENTS, encoding='utf-8')))} 大会目）")


if __name__ == '__main__':
    main()
