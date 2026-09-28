"""EC・NAC の種目別順位（シーズン・性別ごと）に載っている日本人を inventory/fis_standings_jpn.jsonl に追記する。
Claude in Chrome で人の速度で開いた順位ページから写した内容を記録するだけで、このスクリプトは FIS にアクセスしない。
使い方: python inventory/fis_standings_add.py <season> <cup> <gender> <n_rows> '<json list of JPN rows>'
"""
import datetime, json, os, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fis_standings_jpn.jsonl')
season, cup, gender, n_rows, rows = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), json.loads(sys.argv[5])
rec = {'season': int(season), 'cup': cup, 'gender': gender, 'n_rows': n_rows, 'jpn': rows,
       'checked_at': datetime.date.today().isoformat()}
with open(OUT, 'a', encoding='utf-8') as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + '\n')
print(f"記録 {season} {cup} {gender}: 全 {n_rows} 名中 日本人 {len(rows)} 名")
