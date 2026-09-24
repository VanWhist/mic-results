# -*- coding: utf-8 -*-
"""parse_sajmo の結果を xlsx に書き出す。値のみ（数式なし）。
シート: 各ラウンド×性別（例: 2026_SF-m）+ _meta + Event Info。

ラウンド記号（Q / F / SF）は印字の見出し語ではなく、性別ごとの人数と順序で決める
（最少人数の最終ラウンド＝F、3ラウンドの年だけその後に SF。PDF に前のラウンドが載っていない性別は、
ラウンド数の最も多い性別の記号を後ろから使う）。見出し語は年で違い
（2026 は「決勝」が SF-m、「準決勝」が F-m）、シート名を見出し語で付けると分析側が
年ごとの呼称に引きずられるため。分析はシート名ではなく「ラウンド記号」列と _meta を使う。
使い方: python build_sajmo_xlsx.py <pdf> <out.xlsx> <出典URL>
"""
import sys, os
import openpyxl
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from parse_sajmo import parse_pdf, check

GENDER_CODE = {'男子': 'm', '女子': 'w'}
CODES_BY_ROUNDS = {1: ['F'], 2: ['Q', 'F'], 3: ['Q', 'F', 'SF']}


def plan_sheets(meta, secs):
    """[(section, ラウンド記号, シート名, 備考)] を印字順で返す。性別ごとに人数の多い順＝早いラウンド。"""
    year = (meta.get('date') or '????')[:4]
    by_gender = {}
    for s in secs:
        by_gender.setdefault(s['gender'], []).append(s)
    code_of, note_of = {}, {}
    most = max(len(ss) for ss in by_gender.values())
    if most not in CODES_BY_ROUNDS:
        raise SystemExit('ラウンド数 %d は想定外' % most)
    full = CODES_BY_ROUNDS[most]
    for g, ss in by_gender.items():
        if g not in GENDER_CODE:
            raise SystemExit('性別が決まらないセクションがある: %r %r' % (g, [s.get('heading') for s in ss]))
        counts = [len(s['athletes']) for s in ss]
        if len(set(counts)) != len(counts):
            raise SystemExit('%s: 人数が同じラウンドがあり順序を人数で決められない %s' % (g, counts))
        # ラウンド数が少ない性別は、PDF に前のラウンド（予選など）が載っていない。
        # 最終ラウンド側に揃えて、ラウンド数の最も多い性別の記号を後ろから使う（2026-09-17 決定）。
        # 例: 2019 女子は SF 6・決勝 12 だけ → SF/F（Q/F にすると SF が F、決勝が Q になる）。
        codes = full[len(full) - len(ss):]
        for s, code in zip(sorted(ss, key=lambda s: -len(s['athletes'])), codes):
            code_of[id(s)] = code
            if len(ss) < most:
                note_of[id(s)] = '%s の %s は PDF に含まれない（ラウンド記号は最終ラウンド側に揃えた）' % (g, '・'.join(full[:len(full) - len(ss)]))
    return [(s, code_of[id(s)], '%s_%s-%s' % (year, code_of[id(s)], GENDER_CODE[s['gender']]), note_of.get(id(s), ''))
            for s in secs]


def build(pdf_path, out_path, source_url):
    meta, secs = parse_pdf(pdf_path)
    errs, n = check(meta, secs)
    if errs:
        raise SystemExit('CHECK ERRORS: %s' % errs[:5])
    plan = plan_sheets(meta, secs)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for s, code, name, note in plan:
        nturn = s.get('nturn', 5)
        ws = wb.create_sheet(name)
        hdr = (['Rank', 'Status', 'Bib', 'SAJ No', 'FIS No', 'Name', '所属', 'Club']
               + ['J%d Base' % (i+1) for i in range(nturn)]
               + ['J%d Ded' % (i+1) for i in range(nturn)]
               + ['Turns Total', 'Jump1', 'DD1', 'Air1 Ja', 'Air1 Jb',
                  'Jump2', 'DD2', 'Air2 Ja', 'Air2 Jb', 'Air Total',
                  'Time', 'Time Points', 'Score', 'Tie', 'ラウンド記号'])
        ws.append(hdr)
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF', size=9)
            c.fill = PatternFill('solid', fgColor='1F4E78')
        for a in s['athletes']:
            base = a.get('base') or [None]*nturn
            ded = a.get('ded') or [None]*nturn
            ws.append([a['rank'], a['status'], a['bib'], a.get('sajno'), a.get('fisno'),
                       a['name'], a.get('pref'), a.get('club')]
                      + list(base[:nturn]) + [None]*(nturn-len(base[:nturn]))
                      + list(ded[:nturn]) + [None]*(nturn-len(ded[:nturn]))
                      + [a.get('turns_total'), a.get('jump1'), a.get('dd1'), a.get('j6_1'), a.get('j7_1'),
                         a.get('jump2'), a.get('dd2'), a.get('j6_2'), a.get('j7_2'), a.get('air_total'),
                         a.get('time'), a.get('time_point'), a.get('score'), a.get('tie'), code])
        widths = [5,6,5,9,9,16,7,22] + [7]*(nturn*2) + [8,7,6,7,7,7,6,7,7,8,7,7,8,5,8]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = 'A2'

    wm = wb.create_sheet('_meta')
    wm.append(['年', '大会名', 'ラウンド記号', '性別', '印字の見出し語', '印字のラウンド記号', '人数',
               'シート名', 'ページ', 'ターンジャッジ人数', '出典URL', 'ソースPDF', '備考'])
    for s, code, name, note in plan:
        wm.append([int((meta.get('date') or '0')[:4]), meta.get('title'), code, s['gender'], s.get('heading'),
                   s.get('code'), len(s['athletes']), name, ','.join(str(p) for p in s['pages']),
                   s.get('nturn', 5), source_url, os.path.basename(pdf_path), note])
    for c in wm[1]:
        c.font = Font(bold=True)

    wi = wb.create_sheet('Event Info')
    wi.append(['項目', '値'])
    wi.append(['ソースPDF', os.path.basename(pdf_path)])
    wi.append(['大会名', meta.get('title')])
    wi.append(['会場', meta.get('venue')])
    wi.append(['日付', meta.get('date')])
    wi.append(['CODEX', meta.get('codex')])
    for k in sorted(meta['judges']):
        role, nm = meta['judges'][k]
        wi.append(['%s (%s)' % (k, role), nm.split(') ')[0] + ')' if ') ' in nm else nm])
    wi.append(['内部整合検査', '%d項目 エラー0（TurnsTotal合成・Score合成・順位連続性）' % n])
    wi.column_dimensions['A'].width = 18
    wi.column_dimensions['B'].width = 60
    wb.save(out_path)
    return len(plan), sum(len(s['athletes']) for s in secs)


if __name__ == '__main__':
    if os.path.exists(sys.argv[2]):
        raise SystemExit('出力先が既にある。上書きしない: %s' % sys.argv[2])
    ns, na = build(sys.argv[1], sys.argv[2], sys.argv[3])
    print('OK sheets=%d athletes=%d -> %s' % (ns, na, sys.argv[2]))
