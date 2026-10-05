"""ターン審判 8 人（J1〜J8）・エア審判 4 人（J9〜J12。2 日目は 7 人・3 人の行もある）の報告書（Idre 2020-21 の Open）。tier score（印字の合計点だけ）。

規則の版に無い審判構成なので、審判ごとの点からの再計算はしない（城さんの判断 2026-10-04、大阪のジュニア大会と同じ扱い）。
選手の識別（順位・Bib・FIS コード・名前・国・生年・状態・タイム・タイム点）はパーサ A の読みを使い、パーサ A の列の帯が
合わない合計（エア合計・ターン合計・得点）だけを行から読む。
  1 行目: '… B: <ターン 8 人> <B の合計> <得点>'
  3 行目: '<タイム点> <エア合計> <ターン合計>'
タイム点 + エア合計 + ターン合計 = 得点（印字どうしの整合）を確かめ、合わない・読めない行があれば例外で止める。
"""
import re
import pdfplumber
from . import parser_a

# 2 日目は審判が 7 人・エアが 3 人の選手の行がある（点の数が行ごとに違う）。合計の欄だけを読むので 5〜8 個を許す
RE_LINE1 = re.compile(r'^(?:\d+\s+)?(\d+)\s+(\d{7})\s.*\bB:((?:\s+-?\d+\.\d){5,8})\s+\d+\.\d\s+(\d+\.\d\d)\s*$')
RE_LINE3 = re.compile(r'^(\d+\.\d\d)\s+(\d+\.\d\d)\s+(\d+\.\d\d)\s*$')


def parse_moguls_results(path, pages=None):
    meta, recs = parser_a.parse_moguls_results(path, **({'pages': pages} if pages else {}))
    totals = {}
    with pdfplumber.open(path) as pdf:
        for pg in pdf.pages:
            if pages and pg.page_number not in pages:
                continue
            lines = (pg.extract_text() or '').split('\n')
            for i, l in enumerate(lines):
                m = RE_LINE1.match(l)
                if not m:
                    continue
                m3 = RE_LINE3.match(lines[i + 2]) if i + 2 < len(lines) else None
                if not m3:
                    raise ValueError(f"panel8: 合計の行が読めない p{pg.page_number}: {l[:60]} / {lines[i + 2][:40] if i + 2 < len(lines) else ''}")
                totals[(int(m.group(1)), m.group(2))] = (float(m.group(4)), float(m3.group(1)), float(m3.group(2)), float(m3.group(3)))
    used = set()
    for r in recs:
        r['base_scores'], r['ded_scores'], r['air_jumps'] = [], [], []
        r['base_total'] = r['ded_total'] = None
        if r['status'] != 'OK':
            continue
        key = (r['bib'], str(r['fis_code']))
        if key not in totals:
            raise ValueError(f"panel8: {r['name']}（Bib {r['bib']}）の得点の行が無い")
        score, tp, air, turns = totals[key]
        if abs(tp + air + turns - score) > 0.005:
            raise ValueError(f"panel8: {r['name']} タイム点 {tp} + エア {air} + ターン {turns} が得点 {score} と合わない")
        if r['time_points'] is not None and abs(r['time_points'] - tp) > 0.005:
            raise ValueError(f"panel8: {r['name']} タイム点の読みが合わない（A {r['time_points']} / 行 {tp}）")
        r['run_score'], r['air_total'], r['turns_total'] = score, air, turns
        used.add(key)
    if set(totals) - used:
        raise ValueError(f"panel8: 選手の行に当たらない得点の行 {sorted(set(totals) - used)[:5]}")
    meta['parser_version'] = 'P8-1.0'
    return meta, recs
