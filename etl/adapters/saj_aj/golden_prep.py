# -*- coding: utf-8 -*-
"""ゴールデン用の画像を切り出す（値は画像を目で読んで golden_YYYY.json に書く。文字層の値は使わない）。

2025年の読み違い（取り消し線つきの 2.0 を 2.9 と誤読）と、競技役員欄の同姓人名を拾った事故を受けて固定した手順:
  ・対象行の位置の特定にだけ文字層を使う（verify_nc.read_pdf の選手ブロック＝表の内側）
  ・切り取りは選手ブロックの上下だけ（表の外は含めない）
  ・左（順位〜ターン）と右（エア〜スコア）は scale 6、ターン欄は必ず scale 10
使い方: python golden_prep.py <pdf> <出力フォルダ>
"""
import sys, os, re
import pypdfium2 as pdfium
from verify_nc import read_pdf, group_rounds, GENDER_CODE

PDF, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
year, pages, tables = read_pdf(PDF)
rounds, _ = group_rounds(tables)

picked = {}


def pick(r, b, why):
    sheet = '%s_%s-%s' % (year, r['code'], GENDER_CODE[r['gender']])
    key = (sheet, b['bib'])
    picked.setdefault(key, dict(sheet=sheet, bib=b['bib'], page=b['page'],
                                top=b['lines'][0]['top'], bottom=b['lines'][-1]['top'], why=[]))
    picked[key]['why'].append(why)


for r in rounds:
    bl = r['blocks']
    ok = [b for b in bl if not any(t in ('DNF', 'DNS', 'DSQ', 'DQ') for t in b['tokens'])]
    if ok:
        pick(r, ok[0], '%s 1位（優勝ラインの基準行）' % r['code'])
    pick(r, bl[-1], 'ラウンドの最終行')
    for i, b in enumerate(bl):
        toks, joined = b['tokens'], ' '.join(b['tokens'])
        if '西沢' in joined or 'NISHIZAWA' in joined:
            pick(r, b, '西沢選手')
        if i and b['page'] != bl[i - 1]['page']:
            pick(r, bl[i - 1], 'ページ境界の直前')
            pick(r, b, 'ページ境界の直後')
        if any(t in ('DNF', 'DNS', 'DSQ', 'DQ') for t in toks):
            pick(r, b, 'DNF/DNS/DSQ')
        if any(re.fullmatch(r'T\d+', t) for t in toks):
            pick(r, b, '同点（T 印字）')
        if '*NJ' in toks:
            pick(r, b, '*NJ')
        if len(toks) > 2 and not re.fullmatch(r'\d{7}', toks[2]) and not re.fullmatch(r'\d{7}', toks[1]):
            pick(r, b, 'SAJ番号が7桁数字でない')
    # 同じページに別の表が続くときの境目
    for t in r['tables']:
        others = [u for u in tables if u is not t and u['page'] == t['page']]
        if others and t['blocks']:
            pick(r, t['blocks'][-1], '同じページの表の境目（直前の表の最終行）')

pdf = pdfium.PdfDocument(PDF)
cache = {}
for key, p in sorted(picked.items(), key=lambda kv: (kv[1]['page'], kv[1]['top'])):
    pi = p['page'] - 1
    if pi not in cache:
        cache.clear()
        cache[pi] = {s: pdf[pi].render(scale=s).to_pil() for s in (6, 10)}
    top, bot = p['top'] - 4, p['bottom'] + 10
    base = os.path.join(OUT, '%s_%s' % (p['sheet'], p['bib']))
    im = cache[pi][6]
    im.crop((int(18 * 6), int(top * 6), int(350 * 6), int(bot * 6))).save(base + '_左.png')
    im.crop((int(320 * 6), int(top * 6), int(590 * 6), int(bot * 6))).save(base + '_右.png')
    im = cache[pi][10]
    im.crop((int(190 * 10), int(top * 10), int(350 * 10), int(bot * 10))).save(base + '_ターン拡大.png')
    print('%-10s BIB %-3s p%d top=%.0f  %s' % (p['sheet'], p['bib'], p['page'], p['top'], ' / '.join(p['why'])))
