"""FIS サイトから保存した海外大会（EC・NAC・ANC・WJC・UVS）の PDF から registry を生成する。

    python -m etl.adapters.fis_pdf.gen_registry

- 入力: inventory/fis_pdf_manifest.csv（保存した PDF と置き場所。inventory/fis_pdf_organize.py が作る）と
  inventory/fis_overseas_survey.jsonl（大会ページ・レースページで確認したレース一覧）。
- 出力: etl/registry/fis_overseas.json（adapter fis_pdf、tier detail）。
- 1 大会 = FIS の event（開催地・日程のまとまり）× 同じ日の男女。同じ event に第1戦・第2戦があれば別の大会にする
  （codex の小さいほうの男子を代表にした event_id）。
- 今は「ラウンドごとの報告書（予選 RLQ/QRL、決勝 RLF1/F1RL・RLF2/F2RL）がそろい、規則の版が rulesets.json にある」
  モーグル単走だけを登録する。総合（RLF/FRL）はラウンドの記録を並べ直したものなので、ラウンドごとの報告書があれば使わない。
  総合しか無い大会（ANC など）・古い様式・デュアルモーグルは対応したら足す。
既存の registry にある event は、手で書いた項目（PRESERVE）を保持する。
"""
import csv, json, os, re, sys, collections
import pdfplumber
from ... import config
from ...normalize import sha256_file

sys.stdout.reconfigure(encoding='utf-8')
INV = os.path.join(config.REPO, 'inventory')
MANIFEST = os.path.join(INV, 'fis_pdf_manifest.csv')
SURVEY = os.path.join(INV, 'fis_overseas_survey.jsonl')
OUT = os.path.join(config.REGISTRY_DIR, 'fis_overseas.json')
PRESERVE = ('rules', 'notes', 'tier', 'name_ja', 'skip', 'tie_break', 'exclude_pdfs', 'rank_exceptions',
            'recompute_exceptions', 'layer5_exceptions', 'layer5_skip', 'competitor_count_exceptions', 'date_fallback')
ROUND_OF = {'RLQ': 'Q', 'QRL': 'Q', 'RLQ1': 'Q1', 'RLQ2': 'Q2', 'RLF1': 'F1', 'F1RL': 'F1', 'RLF2': 'F2', 'F2RL': 'F2'}
FINAL_ONLY = ('RLF', 'FRL')
RACE_URL = 'https://www.fis-ski.com/DB/general/results.html?sectorcode=FS&raceid={}'


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def n_competitors(path, layout=None):
    """決勝の報告書に印字された出場人数（Number of Competitors）。勝ち上がり人数の登録に使う。
    文字表（layout ascii）には出場人数の印字が無いので、進出人数の印字（Cutoff）か、dns 以外の人数"""
    if layout == 'ascii':
        from . import ascii_a
        meta, recs = ascii_a.parse_moguls_results(path)
        return meta.get('cutoff') or sum(1 for r in recs if r['status'] != 'DNS') or None
    with pdfplumber.open(path) as pdf:
        m = re.search(r'Number of Competitors:\s*(\d+)', pdf.pages[0].extract_text() or '')
    return int(m.group(1)) if m else None


def overall_counts(path):
    """総合の報告から組み立てるラウンドごとの人数（アダプタと同じ分け方）"""
    from .adapter import split_overall, partial_codes
    from . import parser_a
    meta, recs = parser_a.parse_moguls_results(path)
    groups, _ = split_overall(recs, meta, 'A')
    partial = partial_codes(recs)
    # 一部のラウンド（先へ進んだ選手の走りが無い）は人数が勝ち上がり人数にならないので数えない
    return {code: len({r['bib'] for r in grp}) for code, grp in groups.items() if code not in partial}


def advance_of(pdfs):
    """{性別: {前のラウンド: {'to': 次, 'n': 次のラウンドの出場人数}}}。第3層で「前のラウンドの上位 n 人＝次の出場者」を確かめる。
    人数は次のラウンドの報告書の表頭の印字、総合の報告から組み立てるラウンドはその人数"""
    adv = {}
    for g in ('M', 'W'):
        rounds = {p['round']: p for p in pdfs if p['gender'] == g and p['round'] != 'overall'}
        ov = next((p for p in pdfs if p['gender'] == g and p['round'] == 'overall'), None)
        counts = overall_counts(os.path.join(config.PDF_ROOT, ov['path'])) if ov else {}
        a = {}
        for frm, to in (('Q', 'F1'), ('Q1', 'F1'), ('F1', 'F2'), ('F2', 'F3')):
            if (frm in rounds or frm in counts) and (to in rounds or to in counts):
                n = n_competitors(os.path.join(config.PDF_ROOT, rounds[to]['path']), rounds[to].get('layout')) if to in rounds else counts[to]
                if n:
                    a[frm] = {'to': to, 'n': n}
        if a:
            adv[g] = a
    return adv


def layout_family(path):
    """PDF の様式の系統。fis_std（FIS 標準: Rank Bib 表頭・B:/D: 行）、ascii（「=== ===」の文字表。カナダの集計ソフト。
    今の採点方式の表頭のもの。それ以外は ascii_old）、
    ffs（フランスの集計ソフト、仏語）、fis_old（FIS 標準の旧版 2014-16）、other"""
    with pdfplumber.open(path) as pdf:
        tx = '\n'.join((pg.extract_text() or '') for pg in pdf.pages[:2])
    if re.search(r'^=== ===', tx, re.M):
        # 今の採点方式の文字表（NAC 2022）は表頭がこの並び。ANC 2017（エア審判 1 人 2 本・2 本の良い方）・
        # 世界ジュニア 2012（旧採点方式、Event 列あり）は別の様式
        if re.search(r'J\.5\s+(?:T&L|Tec)\s+J\.6\s+J\.7\s+(?:Jumps|Sauts)\s+(?:DofD|DD)\s+(?:Airs|Saut)\s+(?:Judge|Juge)\s+'
                     r'(?:Time|Temps)\s+Pts\s+(?:Run|Desc)\s*$', tx, re.M):
            return 'ascii'
        return 'ascii_old'
    if 'Place Dos Code' in tx or ('Juge 1' in tx and 'Résultats' in tx):
        return 'ffs'
    if 'B:' in tx and 'D:' in tx and re.search(r'Rank\s+Bib', tx):
        return 'fis_std'
    # FIS 標準の旧版（2014-15・2015-16。B: / D: の印が無い 1 人 2 行）。「Percent」の列がある 30 点満点の版（2012-14）は
    # 取り込まない（城さんの判断、2026-09-28）
    if re.search(r'^Rank Bib Name YB Time Score Tie\s*$', tx, re.M) and re.search(r'^Code Code J1 J2 J3 ', tx, re.M):
        return 'fis_old'
    return 'other'


def frl_round(path):
    """旧版の様式（fis_old）の FRL は、見出しが「Ladies' Moguls Final 2」なら決勝 2 だけの報告（NAC 2015 Apex）。
    戻り値: 'F1' / 'F2' / None（総合の報告）"""
    with pdfplumber.open(path) as pdf:
        m = re.search(r"Moguls\s+Final\s*(\d)\s*$", pdf.pages[0].extract_text() or '', re.M)
    return f"F{m.group(1)}" if m else None


def rules_versions():
    return set(load_json(os.path.join(config.RULES_DIR, 'rulesets.json'), {}).get('versions', {}))


def main():
    rows = list(csv.DictReader(open(MANIFEST, encoding='utf-8-sig')))
    events = {}
    for line in open(SURVEY, encoding='utf-8'):
        ev = json.loads(line)
        if not ev.get('supplement'):
            events[ev['event_id']] = ev
    versions = rules_versions()
    # (fis event, codex) -> files
    races = collections.defaultdict(list)
    for r in rows:
        name = os.path.basename(r['dest'])[:-4]
        pre, cat, place, codex, g, disc, typ = name.split('_')
        races[(r['event'], codex)].append(dict(rel=r['dest'].replace('\\', '/'), g=g, disc=disc, typ=typ, raceid=r['raceid'],
                                               season_end=int(pre[3:]), cat=cat, place=place))
    old = {e['event_id']: e for e in load_json(OUT, {}).get('events', [])}
    out, skipped = [], collections.Counter()
    # 同じ FIS event の中で、同じ日付（codex の並び）の男女を 1 大会にまとめる。survey の races の順は男女が隣り合う
    by_event = collections.defaultdict(list)
    for (fis_ev, codex), files in races.items():
        by_event[fis_ev].append((codex, files))
    for fis_ev, items in sorted(by_event.items()):
        sv = events.get(fis_ev, {})
        mo = [(c, f) for c, f in items if f[0]['disc'] == 'MO']
        if not mo:
            continue
        # 男女の組: survey のレース順（raceid の近い男女）で 2 つずつ。codex が隣り合う組を作る
        mo.sort(key=lambda x: int(x[0]))
        groups, used = [], set()
        for c, f in mo:
            if c in used:
                continue
            g0 = f[0]['g']
            mate = next((c2 for c2, f2 in mo if c2 not in used and c2 != c and f2[0]['g'] != g0 and abs(int(c2) - int(c)) <= 2), None)
            grp = [c] + ([mate] if mate else [])
            used.update(grp)
            groups.append(grp)
        for grp in groups:
            files = [x for c in grp for x in dict(items)[c]]
            f0 = files[0]
            season = f"{f0['season_end'] - 1}-{str(f0['season_end'])[2:]}"
            rep = min(grp, key=lambda c: (dict(items)[c][0]['g'] != 'M', int(c)))
            event_id = f"{season}-{f0['cat'].lower()}-{f0['place'].lower()}-{rep}"
            pdfs, reasons = [], []
            for c in sorted(grp):
                fl = dict(items)[c]
                types = {x['typ'] for x in fl}
                for x in fl:
                    if x['typ'] in FINAL_ONLY and layout_family(os.path.join(config.PDF_ROOT, x['rel'])) == 'fis_old':
                        x['round_override'] = frl_round(os.path.join(config.PDF_ROOT, x['rel']))
                per_round = [x for x in fl if x['typ'] in ROUND_OF or x.get('round_override')]
                # 総合（RLF/FRL）: 決勝のラウンドごとの報告書が無いときだけ使う（ANC など。予選の報告書があればそれは別に使う）
                overall = [x for x in fl if x['typ'] in FINAL_ONLY]
                round_of = lambda x: x.get('round_override') or ROUND_OF.get(x['typ'], 'overall')
                overall = [x for x in overall if not x.get('round_override')]
                has_final = any(round_of(x).startswith('F') for x in per_round)
                use = per_round + ([] if has_final else overall[:1])
                fams = {layout_family(os.path.join(config.PDF_ROOT, x['rel'])) for x in use}
                fam = fams.pop() if len(fams) == 1 else (sorted(fams) or [None])[0]
                if fam not in ('fis_std', 'ascii', 'fis_old') or fams:
                    reasons.append(f"{c}: FIS 標準以外の様式（{fam}）はまだ読めない")
                    continue
                if not use:
                    reasons.append(f"{c}: 使える報告書が無い（{sorted(types)}）")
                    continue
                for x in sorted(use, key=lambda x: config.ROUND_ORDER.index(round_of(x)) if round_of(x) in config.ROUND_ORDER else 99):
                    path = os.path.join(config.PDF_ROOT, x['rel'])
                    pdfs.append({'path': x['rel'], 'sha256': sha256_file(path), 'url': None,
                                 'page_url': RACE_URL.format(x['raceid']), 'round': round_of(x),
                                 'gender': x['g'], 'codex': c, 'src_type': x['typ']})
                    if fam in ('ascii', 'fis_old'):
                        pdfs[-1]['layout'] = fam
            if season not in versions:
                reasons.append(f"規則の版 {season} が rulesets.json に無い")
            if reasons or not pdfs:
                for rs in reasons:
                    skipped[rs.split('（')[0].split(': ', 1)[-1]] += 1
                continue
            ev = {'event_id': event_id, 'season': season, 'series': f0['cat'], 'grade': None, 'discipline': 'MO',
                  'name_ja': None, 'place': sv.get('place') or f0['place'], 'fis_event_id': fis_ev,
                  'page_url': f"https://www.fis-ski.com/DB/general/event-details.html?sectorcode=FS&eventid={fis_ev}&seasoncode={f0['season_end']}",
                  'tier': 'detail', 'adapter': 'fis_pdf', 'rules': season, 'pdfs': pdfs,
                  'format': {'label': None, 'advance': advance_of(pdfs),
                             'advance_basis': '次のラウンドの報告書に印字された出場人数'}, 'notes': ''}
            if event_id in old:
                for k in PRESERVE:
                    if k in old[event_id]:
                        ev[k] = old[event_id][k]
                # format は印字から毎回作る。手で直したときは format.manual = true にすれば保持する
                if (old[event_id].get('format') or {}).get('manual'):
                    ev['format'] = old[event_id]['format']
            out.append(ev)
    with open(OUT, 'w', encoding='utf-8') as fh:
        json.dump({'_about': 'FIS サイトから保存した海外大会（etl/adapters/fis_pdf/gen_registry.py が生成。手書き項目は保持）',
                   'events': out}, fh, ensure_ascii=False, indent=1)
        fh.write('\n')
    print(f"登録 {len(out)} 大会 / {sum(len(e['pdfs']) for e in out)} PDF → {os.path.relpath(OUT, config.REPO)}")
    for k, v in skipped.most_common():
        print(f"  見送り {v}: {k}")


if __name__ == '__main__':
    main()
