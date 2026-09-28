"""ダウンロードフォルダの FIS 結果 PDF を その他大会のリザルト/<系列>/<シーズン>/ に名前をそろえてコピーする。
既定は下見（manifest を書くだけ）。--apply でコピー。元ファイルは消さない。"""
import json, os, re, sys, shutil, unicodedata, csv, hashlib
import pdfplumber
SURVEY = r'D:\Claude\ジャッジ分析\mic-results\inventory\fis_overseas_survey.jsonl'
DL = r'C:\Users\yuyu_\Downloads'
ROOT = r'D:\Claude\ジャッジ分析\その他大会のリザルト'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fis_pdf_manifest.csv')

PLAN = r'D:/Claude/ジャッジ分析/mic-results/inventory/fis_download_plan.json'
_plan = json.load(open(PLAN, encoding='utf-8'))
_plan = _plan if isinstance(_plan, list) else _plan.get('events', _plan)
PLAN_CAT = {e['event_id']: [c for c in e['categories'] if c != 'FIS'][0] for e in _plan}
races = {}
for l in open(SURVEY, encoding='utf-8'):
    ev = json.loads(l)
    if ev.get('supplement'):
        continue
    for r in ev['races']:
        races[(ev.get('season'), r['codex'])] = dict(event_id=ev['event_id'], place=ev.get('place'), cat=ev.get('cat') or PLAN_CAT.get(ev['event_id']),
                                                     season=ev.get('season'), race=r.get('race', ''), raceid=r['raceid'])

def slug(place):
    p = place.split(',')[0]
    p = unicodedata.normalize('NFKD', p).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]', '', p.title().replace(' ', ''))

def pdf_gd(path):
    with pdfplumber.open(path) as pdf:
        t = (pdf.pages[0].extract_text() or '')[:1500]
    tl = t.lower().replace('’', "'")
    g = None
    # 性別は見出しの語か、ANC 2017 の (F0000)/(M0000) で見る。'individual' に 'dual' が含まれるので語頭で見る
    if re.search(r"(women|ladies|lady)'?s?", tl) or '(f0000)' in tl: g = 'W'
    elif re.search(r"\bmen'?s?\b", tl) or '(m0000)' in tl: g = 'M'
    d = 'DM' if re.search(r'\bdual|bracket|ladder', tl) else ('MO' if 'mogul' in tl else None)
    return g, d, t.split('\n')[:3]

pat = re.compile(r'^(\d{4})FS(\d{4})([A-Z0-9]+)\.pdf$', re.I)
rows = []
for f in sorted(os.listdir(DL)):
    m = pat.match(f)
    if not m: continue
    season, codex, typ = int(m.group(1)), m.group(2), m.group(3).upper()
    info = races.get((season, codex))
    if not info: continue
    g, d, head = pdf_gd(os.path.join(DL, f))
    lab = info['race']
    lg = ld = None
    mm = re.match(r'^([MW]) (MO|DM)$', lab)
    if mm: lg, ld = mm.groups()
    elif lab:
        lg = 'W' if "Women" in lab else 'M'; ld = 'DM' if 'Dual' in lab else 'MO'
    status = 'ok'
    if lg and g and lg != g: status = 'GENDER_MISMATCH'
    if ld and d and ld != d: status = 'DISC_MISMATCH' if status == 'ok' else status + '+DISC'
    # 大会ページのレース名（記録）を正とする。PDF の見出しは誤植がある（2016 Are EC 8470 は男子 DM なのに Women's Duals）
    gg, dd = lg or g, ld or d
    if not gg or not dd: status = 'UNKNOWN'
    sea = f'{season-1}-{str(season)[2:]}'
    name = f"FIS{season}_{info['cat']}_{slug(info['place'])}_{codex}_{gg}_{dd}_{typ}.pdf"
    dest = os.path.join(info['cat'], sea, name)
    rows.append(dict(src=f, dest=dest, status=status, label=lab, pdf_g=g, pdf_d=d, event=info['event_id'], raceid=info['raceid'], head=' / '.join(head)))

with open(OUT, 'w', newline='', encoding='utf-8-sig') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
from collections import Counter
print(Counter(r['status'] for r in rows), len(rows))
for r in rows:
    if r['status'] != 'ok': print(r['status'], r['src'], r['label'], r['pdf_g'], r['pdf_d'], '|', r['head'][:120])
dests = Counter(r['dest'] for r in rows)
print('dup dests', [d for d, c in dests.items() if c > 1])
if '--apply' in sys.argv:
    n = 0
    for r in rows:
        dst = os.path.join(ROOT, r['dest'])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            same = hashlib.sha256(open(dst,'rb').read()).digest() == hashlib.sha256(open(os.path.join(DL, r['src']),'rb').read()).digest()
            print('exists', r['dest'], 'same' if same else 'DIFFERENT'); continue
        shutil.copy2(os.path.join(DL, r['src']), dst); n += 1
    print('copied', n)
