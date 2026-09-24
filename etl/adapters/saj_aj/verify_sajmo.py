# -*- coding: utf-8 -*-
"""PDFを独立に再パースし、保存済みxlsxの全セルと突合する。"""
import sys
import openpyxl
from parse_sajmo import parse_pdf
from build_sajmo_xlsx import plan_sheets

def close(a, b):
    if a is None and (b is None or b == ''):
        return True
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a or '') == str(b or '')

def main(pdf_path, xlsx_path):
    meta, secs = parse_pdf(pdf_path)
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    mism = []
    total = 0
    for s, code, name, _note in plan_sheets(meta, secs):
        nturn = s.get('nturn', 5)
        ws = wb[name]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        if len(rows) != len(s['athletes']):
            mism.append('%s: row count %d != %d' % (name, len(rows), len(s['athletes'])))
            continue
        for a, row in zip(s['athletes'], rows):
            base = a.get('base') or [None]*nturn
            ded = a.get('ded') or [None]*nturn
            expected = ([a['rank'], a['status'], a['bib'], a.get('sajno'), a.get('fisno'),
                         a['name'], a.get('pref'), a.get('club')]
                        + list(base[:nturn]) + [None]*(nturn-len(base[:nturn]))
                        + list(ded[:nturn]) + [None]*(nturn-len(ded[:nturn]))
                        + [a.get('turns_total'), a.get('jump1'), a.get('dd1'), a.get('j6_1'), a.get('j7_1'),
                           a.get('jump2'), a.get('dd2'), a.get('j6_2'), a.get('j7_2'), a.get('air_total'),
                           a.get('time'), a.get('time_point'), a.get('score'), a.get('tie'), code])
            for i, (e, g) in enumerate(zip(expected, row)):
                total += 1
                if not close(e, g):
                    mism.append('%s %s col%d: pdf=%r xlsx=%r' % (name, a['name'], i+1, e, g))
    if mism:
        print('MISMATCHES: %d / %d cells' % (len(mism), total))
        for m in mism[:20]:
            print(' ', m)
        sys.exit(1)
    print('ALL VALUES MATCH - %d cells verified.' % total)

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
