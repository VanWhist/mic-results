"""検証レポートのエラーを大会ごとに要約する（開発用）: python -m etl.tests.err_summary [season-prefix] [max]"""
import sys, re, collections, json, os
sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
pref = sys.argv[1] if len(sys.argv) > 1 else ''
mx = int(sys.argv[2]) if len(sys.argv) > 2 else 3
t = open(os.path.join(HERE, '..', '..', 'docs', '検証レポート.md'), encoding='utf-8').read()
errs = [l for l in t.split('## エラー')[1].split('## 警告')[0].splitlines() if l.startswith('- ')]
by = collections.defaultdict(list)
for l in errs:
    m = re.match(r'- \[(\w+)\] (\S+): (.*)', l)
    if m.group(2).startswith(pref):
        by[m.group(2)].append((m.group(1), m.group(3)))
reg = {}
for fn in os.listdir(os.path.join(HERE, '..', 'registry')):
    for e in json.load(open(os.path.join(HERE, '..', 'registry', fn), encoding='utf-8')).get('events', []):
        reg[e['event_id']] = e
print(f"エラーのある round/event: {len(by)} / 行 {sum(len(v) for v in by.values())}")
for k, v in sorted(by.items()):
    ev = reg.get(k) or reg.get(k.rsplit('-', 2)[0])
    print(f"\n{k}  ({len(v)})  {ev['name_ja'][:40] if ev else ''}  {[p['path'] for p in ev['pdfs']] if ev else ''}")
    for lay, msg in v[:mx]:
        print(f"   [{lay}] {msg[:170]}")
