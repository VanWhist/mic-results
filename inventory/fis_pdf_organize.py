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
        cat = ev.get('cat') or PLAN_CAT.get(ev['event_id'])
        # 区分が 'OPN/FIS'・'NC,FIS' の大会（FIS 格・Open の調査）は、レース名の末尾の区分（'… Moguls | OPN'）を使う
        rc = re.search(r'\|\s*([A-Z]{2,4})\s*$', r.get('race', ''))
        if cat and re.search(r'[/,]', cat) and rc:
            cat = rc.group(1)
        races[(ev.get('season'), r['codex'])] = dict(event_id=ev['event_id'], place=ev.get('place'), cat=cat,
                                                     season=ev.get('season'), race=r.get('race', ''), raceid=r['raceid'])

# FIS の名前（<season>FS<codex><type>.pdf）でない保存済みの PDF（FIS 格・Open の調査の前に保存したもの）。
# 名前 → (シーズン, codex, 報告書の種類)。中身の見出し（大会名・日付・性別・ラウンド）で確かめた（2026-10-04）
LOCAL = {}
for d, season, codex in (('2018-12-15', 2019, '8410'), ('2019-12-14', 2020, '8457'), ('2019-12-15', 2020, '8459'),
                         ('2022-12-17', 2023, '8580'), ('2022-12-18', 2023, '8582')):
    for kind, typ in (('Final', 'RLF'), ('Qualification', 'RLQ'), ('QualificationRun1', 'RLQ1'), ('QualificationRun2', 'RLQ2'),
                      ('FinalRun1', 'RLF1')):
        LOCAL[f'{d}_ApexMountainBC_FIS_MO_{kind}.pdf'] = (season, codex, typ)
for d, codex in (('2020-11-21', '8387'), ('2020-11-22', '8389')):
    LOCAL[f'{d}_IdreFjll_OPN_MO_FinalRun1.pdf'] = (2021, codex, 'RLF1')
    LOCAL[f'{d}_IdreFjll_OPN_MO_Qualification.pdf'] = (2021, codex, 'RLQ')
for kind, typ in (('Final', 'RLF'), ('FinalRun1', 'RLF1'), ('FinalRun2', 'RLF2'), ('Qualification', 'RLQ')):
    LOCAL[f'2023-12-16_ApexMountainBC_OPN_MO_{kind}.pdf'] = (2024, '8605', typ)
LOCAL['2023-12-17_ApexMountainBC_OPN_DM_Final.pdf'] = (2024, '8607', 'RLF')
LOCAL['2023-12-17_ApexMountainBC_OPN_DM_Qualification.pdf'] = (2024, '8607', 'RLQ')
LOCAL['2025-11-22_IdreFjall_OPN_MO_Final.pdf'] = (2026, '8491', 'RLF')
LOCAL['MISE_17810_Final.pdf'] = (2025, '8622', 'RLF')
LOCAL['MISE_17810_Qualification.pdf'] = (2025, '8622', 'RLQ')
LOCAL['MISE_17816_Final.pdf'] = (2025, '8621', 'RLF')
LOCAL['MISE_17816_BRFinal.pdf'] = (2025, '8621', 'RBLF')

def slug(place):
    p = place.split(',')[0]
    p = unicodedata.normalize('NFKD', p).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9]', '', p.title().replace(' ', ''))

def pdf_gd(path):
    with pdfplumber.open(path) as pdf:
        t = (pdf.pages[0].extract_text() or '')[:1500]
    tl = t.lower().replace('’', "'")
    g = None
    # 見出しの「Men's Moguls」「Ladies' Moguls」を最優先（ANC 2018 の男子 8029・8031 は本文の別の語で女子と誤判定していた）
    mh = re.search(r"\b(men|women|ladies)'?s?\s+(?:dual\s+)?moguls", tl)
    if mh:
        return ('M' if mh.group(1) == 'men' else 'W'), ('DM' if 'dual' in mh.group(0) else 'MO'), t.split('\n')[:3]
    # 性別は見出しの語か、ANC 2017 の (F0000)/(M0000) で見る。'individual' に 'dual' が含まれるので語頭で見る
    if re.search(r"(women|ladies|lady)'?s?", tl) or '(f0000)' in tl: g = 'W'
    elif re.search(r"\bmen'?s?\b", tl) or '(m0000)' in tl: g = 'M'
    d = 'DM' if re.search(r'\bdual|bracket|ladder', tl) else ('MO' if 'mogul' in tl else None)
    return g, d, t.split('\n')[:3]

pat = re.compile(r'^(\d{4})FS(\d{4})([A-Z0-9]+)\.pdf$', re.I)
rows = []
for f in sorted(os.listdir(DL)):
    m = pat.match(f)
    if m:
        season, codex, typ = int(m.group(1)), m.group(2), m.group(3).upper()
    elif f in LOCAL:
        season, codex, typ = LOCAL[f]
    else:
        continue
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
