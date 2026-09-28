"""海外 FIS 大会の日本人出場の調査結果を 1 大会ずつ inventory/fis_overseas_survey.jsonl に追記する。
Claude in Chrome で人の速度（1 ページずつ）で開いた大会ページ・レース結果ページから写した内容を記録するだけで、
このスクリプト自体は FIS にアクセスしない。
使い方: python inventory/fis_survey_add.py '<json>'
  json = {"event_id": "...", "races": [{"raceid": "...", "codex": "...", "race": "02.02.2026 - Men's Moguls | EC",
          "n": 50, "jpn": ["rank bib fiscode NAME ..."], "dls": ["Results - Final download (449.46 kb)"]}], "note": "..."}
"""
import datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fis_overseas_survey.jsonl')


def main():
    rec = json.loads(sys.argv[1])
    rec['checked_at'] = datetime.date.today().isoformat()
    rec['jpn_total'] = sum(len(r.get('jpn') or []) for r in rec.get('races', []))
    with open(OUT, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
    done = sum(1 for _ in open(OUT, encoding='utf-8'))
    print(f"記録 {rec['event_id']}: 日本人 {rec['jpn_total']} 名（{done} 大会目）")


if __name__ == '__main__':
    main()
