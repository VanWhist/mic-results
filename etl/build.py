"""mic-results ETL: registry -> adapters -> 多層照合 -> data/*.json

    python -m etl.build [--accept-rounds] [--accept-revision] [--adapter saj_aj] [--event <substr>]

Every event comes from ``etl/registry/*.json``. Each registry entry names its adapter
(``etl/adapters/<name>/adapter.py`` with ``load_event(ev, imported_at, log)``), which returns round
contexts in the shared shape (see normalize.py). Verification and publication are tier-aware.
"""
import argparse, collections, datetime, hashlib, importlib, json, os, sys

from . import config, normalize, verify

BUILD_VERSION = '2026-09-25-1'


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def dump_json(path, obj):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=0, separators=(',', ':'))


def content_hash(obj):
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(s.encode('utf-8')).hexdigest()[:8]


def load_registry(adapter_filter=None, event_filter=None):
    events = []
    for fn in sorted(os.listdir(config.REGISTRY_DIR)):
        if not fn.endswith('.json'):
            continue
        reg = load_json(os.path.join(config.REGISTRY_DIR, fn), {})
        for ev in reg.get('events', []):
            if adapter_filter and ev['adapter'] != adapter_filter:
                continue
            if event_filter and event_filter not in ev['event_id']:
                continue
            events.append(ev)
    return events


def advance_for(ev, gender):
    fmt = ev.get('format') or {}
    adv = fmt.get('advance') or {}
    if gender in adv:
        return adv[gender]
    return adv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--accept-rounds', action='store_true', help='新しいラウンドの人数を基準として登録する')
    ap.add_argument('--accept-revision', action='store_true', help='元PDFの変更（公式改訂）を受け入れて基準ハッシュを更新する')
    ap.add_argument('--adapter', default=None, help='このアダプタの大会だけ処理（開発用。公開ゲートは無効）')
    ap.add_argument('--event', default=None, help='event_id にこの文字列を含む大会だけ処理（開発用。公開ゲートは無効）')
    args = ap.parse_args(argv)
    dev = bool(args.adapter or args.event)

    expected = load_json(config.EXPECTED_ROUNDS, {})
    published = load_json(config.PUBLISHED_HASHES, {})
    aliases = load_json(config.ATHLETE_ALIASES, {})
    master = load_json(config.ATHLETE_MASTER, {})
    roster = load_json(config.MIC_ROSTER, {})
    imported_at = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    events = load_registry(args.adapter, args.event)
    print(f"対象大会: {len(events)} 件")
    findings = []
    rounds_ctx = []
    dd_seen = collections.defaultdict(set)
    events_by_id = {ev['event_id']: ev for ev in events}
    for ev in events:
        mod = importlib.import_module(f"etl.adapters.{ev['adapter']}.adapter")
        try:
            ctxs = mod.load_event(ev, imported_at)
        except Exception as e:
            findings.append(verify.Finding('error', ev['event_id'], 'layer0', f"アダプタ例外: {e!r}"))
            continue
        for c in ctxs:
            if c.get('error_only'):
                findings.append(verify.Finding('error', c['event_id'], 'layer0', c['message']))
                continue
            # verify the PDF hash recorded in the registry
            cls = c['cls']
            if cls.get('pdf_sha256'):
                actual = normalize.sha256_file(cls['path'])
                if actual != cls['pdf_sha256']:
                    findings.append(verify.Finding('error', cls['event_id'], 'layer0',
                                                   f"PDF の SHA-256 が registry と違う {cls['rel']}（改訂なら registry を更新）"))
            rnd, runs = normalize.make_round(cls, c['meta'], c['records'], c['rules'], imported_at)
            ctx = {'cls': cls, 'round': rnd, 'runs': runs, 'records_a': c['records'], 'rules': c['rules'],
                   'ab_compared': c.get('ab_compared', False), 'event': ev}
            findings += c.get('findings', [])
            if rnd['tier'] == 'detail':
                findings += verify.layer2(rnd['round_id'], runs, None, rnd['gender'], dd_seen)
            if rnd['tier'] in ('detail', 'score'):
                findings += verify.layer3_rank(rnd['round_id'], runs, c['rules'])
            rounds_ctx.append(ctx)
            print(f"  読込 {rnd['round_id']:34s} {rnd['gender']} {rnd['round']:3s} {len(runs):3d} records  tier={rnd['tier']}")

    findings += verify.layer0(rounds_ctx, expected, args.accept_rounds, check_missing=not dev)
    n_detail = [ctx for ctx in rounds_ctx if ctx['round']['tier'] == 'detail']
    n_ab = sum(1 for ctx in n_detail if ctx.get('ab_compared'))
    if n_ab != len(n_detail):
        findings.append(verify.Finding('error', 'global', 'layer1', f"2方式比較が実行されたラウンド {n_ab}/{len(n_detail)}"))
    findings += verify.dd_consistency(dd_seen)
    for ev in events:
        for gender in ('M', 'W'):
            rbc = {ctx['round']['round']: (ctx['round'], ctx['runs']) for ctx in rounds_ctx
                   if ctx['round']['event_id'] == ev['event_id'] and ctx['round']['gender'] == gender and ctx['round']['tier'] != 'rank'}
            if rbc:
                findings += verify.layer3_progression(ev['event_id'], rbc, advance_for(ev, gender))
    all_runs = [r for ctx in rounds_ctx for r in ctx['runs']]
    all_rounds = [ctx['round'] for ctx in rounds_ctx]
    findings += verify.layer4(all_runs, all_rounds)

    layer5_status = 'skipped'
    layer5_cache = load_json(config.LAYER5_CACHE, {})

    runs_by_id = {r['run_id']: r for r in all_runs}
    gf, n_golden = verify.golden(config.GOLDEN_DIR, runs_by_id, strict=not dev)
    findings += gf

    # ---------------- publication gate
    errors_by_round = collections.defaultdict(list)
    for fd in findings:
        if fd.level == 'error':
            errors_by_round[fd.round_id].append(fd)
    bad_events = set()
    for ctx in rounds_ctx:
        if errors_by_round.get(ctx['round']['round_id']) or errors_by_round.get(ctx['round']['event_id']):
            bad_events.add(ctx['round']['event_id'])
    global_errors = errors_by_round.get('global', [])
    for ctx in rounds_ctx:
        rid = ctx['round']['round_id']
        tier = ctx['round']['tier']
        v = {}
        for layer in verify.LAYERS:
            if layer not in verify.LAYERS_BY_TIER[tier]:
                v[layer] = 'n/a'
                continue
            errs = [x for x in errors_by_round.get(rid, []) if x.layer == layer]
            v[layer] = 'error' if errs else ('skipped' if layer == 'layer5' else 'ok')
        if v.get('layer5') == 'skipped':
            v['layer5'] = layer5_cache.get(rid, 'skipped')
        if n_golden == 0 and v.get('golden') == 'ok':
            v['golden'] = 'skipped'
        ctx['round']['verification'] = v

    for ctx in rounds_ctx:
        rid = ctx['round']['round_id']
        pdf_hash = ctx['round']['source']['pdf_sha256']
        prev = published.get(rid)
        if prev and prev['pdf_sha256'] != pdf_hash:
            if args.accept_revision:
                findings.append(verify.Finding('warning', rid, 'gate', '元 PDF が改訂された（受け入れ）'))
            else:
                findings.append(verify.Finding('error', rid, 'gate', '公開済みラウンドの元 PDF が変わった。内容確認後 `--accept-revision`'))
                bad_events.add(ctx['round']['event_id'])

    publish_ctx = [ctx for ctx in rounds_ctx if ctx['round']['event_id'] not in bad_events]
    if global_errors:
        print(f"!! 全体エラー {len(global_errors)} 件のため公開データを更新しません")
    write_report(findings, rounds_ctx, bad_events, n_golden, layer5_status)
    n_err = sum(1 for x in findings if x.level == 'error')
    n_warn = sum(1 for x in findings if x.level == 'warning')
    print(f"\n{len(all_runs)} records / {n_warn} warnings / {n_err} errors / 公開対象 {len(publish_ctx)}/{len(rounds_ctx)} ラウンド")

    if args.accept_rounds:
        dump_json(config.EXPECTED_ROUNDS, expected)
    if global_errors or dev:
        if dev:
            print("(--adapter/--event 指定のため data/ は更新しません)")
        return 1 if (global_errors or n_err) else 0

    counts = {'runs_parsed': len(all_runs), 'rounds_parsed': len(rounds_ctx),
              'runs_ab_compared': sum(len(ctx['runs']) for ctx in rounds_ctx if ctx.get('ab_compared')),
              'runs_recomputed': sum(1 for r in all_runs if r['status'] == 'OK' and '_recomputed' in r),
              'rounds_ranked': sum(1 for ctx in rounds_ctx if ctx['round']['tier'] != 'rank'),
              'golden_runs': n_golden}
    write_data(publish_ctx, events_by_id, aliases, master, roster, imported_at, n_golden, layer5_status, findings, counts)
    for ctx in publish_ctx:
        rid = ctx['round']['round_id']
        published[rid] = {'pdf_sha256': ctx['round']['source']['pdf_sha256'],
                          'runs_hash': content_hash([strip_private(r) for r in ctx['runs']])}
    dump_json(config.PUBLISHED_HASHES, published)
    return 0 if n_err == 0 else 1


def strip_private(run):
    return {k: v for k, v in run.items() if not k.startswith('_')}


def write_report(findings, rounds_ctx, bad_events, n_golden, layer5_status):
    os.makedirs(config.DOCS_DIR, exist_ok=True)
    lines = [f"# 検証レポート", f"", f"生成: {datetime.datetime.now():%Y-%m-%d %H:%M}", "",
             f"ラウンド {len(rounds_ctx)} 件 / 記録 {sum(len(c['runs']) for c in rounds_ctx)} 本 / 正解データ {n_golden} 本 / 第5層: {layer5_status}", ""]
    tiers = collections.Counter(c['round']['tier'] for c in rounds_ctx)
    lines.append("段階別: " + " / ".join(f"{config.TIER_LABELS[t]} {n}" for t, n in sorted(tiers.items())))
    lines.append("")
    errs = [f for f in findings if f.level == 'error']
    warns = [f for f in findings if f.level == 'warning']
    lines += [f"## エラー（{len(errs)} 件）", ""]
    for f in errs:
        lines.append(f"- [{f.layer}] {f.round_id}: {f.message}")
    lines += ["", f"## 警告（{len(warns)} 件）", ""]
    for f in warns:
        lines.append(f"- [{f.layer}] {f.round_id}: {f.message}")
    lines += ["", "## ラウンド別", "", "| round_id | 段階 | 記録 | " + " | ".join(verify.LAYERS) + " | 公開 |", "|" + "---|" * (len(verify.LAYERS) + 4)]
    for ctx in rounds_ctx:
        r = ctx['round']
        pub = '×' if r['event_id'] in bad_events else '○'
        lines.append(f"| {r['round_id']} | {r['tier']} | {len(ctx['runs'])} | " + " | ".join(r['verification'].get(l, '-') for l in verify.LAYERS) + f" | {pub} |")
    with open(os.path.join(config.DOCS_DIR, '検証レポート.md'), 'w', encoding='utf-8') as fh:
        fh.write("\n".join(lines) + "\n")


def cut_label(advance, round_code):
    adv = (advance or {}).get(round_code)
    if not adv:
        return None
    return {'rank': adv['n'], 'to': adv['to'], 'label': f"{adv['to']} 進出ライン（{adv['n']}位）"}


def mic_status(roster, athlete_id, name):
    for m in roster.get('members', []):
        if m.get('athlete_id') == athlete_id or (name and m.get('name') == name):
            return {'from': m.get('from'), 'to': m.get('to')}
    return None


def write_data(publish_ctx, events_by_id, aliases, master, roster, imported_at, n_golden, layer5_status, findings, counts=None):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    for fn in os.listdir(config.DATA_DIR):
        p = os.path.join(config.DATA_DIR, fn)
        if os.path.isfile(p) and fn != 'manifest.json' and fn.endswith('.json'):
            os.remove(p)

    ev_out = {}
    for ctx in publish_ctx:
        r = ctx['round']
        ev = events_by_id[r['event_id']]
        e = ev_out.setdefault(r['event_id'], {
            'event_id': r['event_id'], 'season': ev['season'], 'series': ev['series'],
            'series_label': config.SERIES_LABELS.get(ev['series'], ev['series']),
            'series_group': config.SERIES_GROUP.get(ev['series'], '国内'),
            'grade': ev.get('grade'), 'discipline': ev.get('discipline', 'MO'), 'name_ja': ev.get('name_ja'),
            'format': ev.get('format'), 'format_label': (ev.get('format') or {}).get('label'),
            'venue': r['venue'], 'nation': 'JPN' if ev['series'].startswith('SAJ') else None,
            'rules_version': ev.get('rules'), 'sources': ev.get('pdfs', []), 'rounds': []})
        e['rounds'].append({k: v for k, v in r.items()})
    for e in ev_out.values():
        e['rounds'].sort(key=lambda r: (r['gender'], config.ROUND_ORDER.index(r['round'])))
        e['date_from'] = min((r['date'] for r in e['rounds'] if r['date']), default=None)
        e['date_to'] = max((r['date'] for r in e['rounds'] if r['date']), default=None)
        e['tiers'] = sorted({r['tier'] for r in e['rounds']})
    events_list = sorted(ev_out.values(), key=lambda e: (e['date_from'] or '', e['event_id']), reverse=True)

    runs_by_season = collections.defaultdict(list)
    all_runs = []
    for ctx in publish_ctx:
        for run in ctx['runs']:
            pub = strip_private(run)
            runs_by_season[run['season']].append(pub)
            all_runs.append(pub)

    lines = []
    for ctx in publish_ctx:
        r = ctx['round']
        ok = [x for x in ctx['runs'] if x['status'] == 'OK' and x['counting'] and x['rank']]
        ok.sort(key=lambda x: x['rank'])
        if not ok:
            continue
        cl = cut_label(advance_for(ctx['event'], r['gender']), r['round'])
        entry = {'round_id': r['round_id'], 'winner': run_summary(ok[0]), 'cut': None, 'n_ok': len(ok)}
        if cl:
            cut_run = next((x for x in ok if x['rank'] == cl['rank']), None)
            if cut_run is None and ok:
                cut_run = max([x for x in ok if x['rank'] <= cl['rank']] or [ok[-1]], key=lambda x: x['rank'])
            entry['cut'] = {'rank': cl['rank'], 'label': cl['label'], 'to': cl['to'], 'run': run_summary(cut_run)}
        lines.append(entry)

    by_id = collections.defaultdict(list)
    for run in all_runs:
        by_id[run['athlete_id']].append(run)
    athletes = []
    for aid, rs in by_id.items():
        rs_sorted = sorted(rs, key=lambda x: x['date'] or '')
        names = collections.Counter(x['name'] for x in rs)
        name = names.most_common(1)[0][0]
        aff_hist = []
        for x in rs_sorted:
            key = (x.get('affiliation'), x.get('club'))
            if not aff_hist or (aff_hist[-1]['affiliation'], aff_hist[-1]['club']) != key:
                aff_hist.append({'affiliation': key[0], 'club': key[1], 'from': x['season'], 'to': x['season']})
            else:
                aff_hist[-1]['to'] = x['season']
        al = aliases.get(aid, {}) if isinstance(aliases, dict) else {}
        alias_list = [n for n in names if n != name] + ([al.get('kana')] if al.get('kana') else []) + list(al.get('kanji', []))
        best = max([x for x in rs if x['run_score'] is not None], key=lambda x: x['run_score'], default=None)
        fis = next((x['fis_code'] for x in rs_sorted if x.get('fis_code')), None)
        saj = next((x['saj_no'] for x in rs_sorted if x.get('saj_no')), None)
        athletes.append({'athlete_id': aid, 'fis_code': fis, 'saj_no': saj, 'name': name, 'aliases': [a for a in alias_list if a],
                         'noc': rs_sorted[-1].get('noc'), 'yb': rs_sorted[-1].get('yb'),
                         'affiliation': rs_sorted[-1].get('affiliation'), 'club': rs_sorted[-1].get('club'),
                         'affiliation_history': aff_hist, 'mic': mic_status(roster, aid, name),
                         'n_results': sum(1 for x in rs if x['counting']), 'seasons': sorted({x['season'] for x in rs}),
                         'series': sorted({x['series'] for x in rs}),
                         'best': {'run_score': best['run_score'], 'run_id': best['run_id']} if best else None})
    athletes.sort(key=lambda a: a['name'])

    judges = {}
    for ctx in publish_ctx:
        r = ctx['round']
        for j in r['judges']:
            e = judges.setdefault(j['judge_id'], {'judge_id': j['judge_id'], 'name': j['name'], 'noc': j['noc'], 'rounds': []})
            e['rounds'].append({'round_id': r['round_id'], 'no': j['no'], 'role': j['role']})
    judges_list = sorted(judges.values(), key=lambda j: j['name'])

    rules_out = {}
    for ctx in publish_ctx:
        rv = ctx['round']['source']['rules_version']
        if rv and rv not in rules_out:
            rules_out[rv] = ctx['rules']

    files = {}

    def emit(name, obj):
        h = content_hash(obj)
        fn = f"{name}.{h}.json"
        dump_json(os.path.join(config.DATA_DIR, fn), obj)
        return fn
    files['events'] = emit('events', events_list)
    files['athletes'] = emit('athletes', athletes)
    files['lines'] = emit('lines', lines)
    files['judges'] = emit('judges', judges_list)
    files['rules'] = emit('rules', rules_out)
    files['runs'] = {season: emit(f'runs.{season}', rs) for season, rs in sorted(runs_by_season.items())}

    manifest = {
        'dataVersion': datetime.datetime.now().strftime('%Y-%m-%d-%H%M'), 'builtAt': imported_at,
        'buildVersion': BUILD_VERSION,
        'files': files,
        'counts': {'events': len(events_list), 'rounds': len(publish_ctx), 'runs': len(all_runs), 'athletes': len(athletes)},
        'seasons': sorted(runs_by_season.keys()),
        'series': sorted({e['series'] for e in events_list}),
        'tiers': {t: config.TIER_LABELS[t] for t in ('detail', 'score', 'rank')},
        'verification': {'allGreen': all(all(v in ('ok', 'skipped', 'upstream_missing', 'n/a') for v in c['round']['verification'].values()) for c in publish_ctx),
                         'layers': {'layer0': '完全性（大会・ラウンド・人数）', 'layer1': '2方式の読み取り一致', 'layer2': '規則からの再計算一致',
                                    'layer3': '順位・進出条件の再構成', 'layer4': '大会横断の整合', 'layer5': '公式Web結果との照合', 'golden': '目視正解データとの一致'},
                         'goldenRuns': n_golden, 'layer5': layer5_status, 'counts': counts or {},
                         'warnings': sum(1 for f in findings if f.level == 'warning'),
                         'reportPath': 'docs/検証レポート.md'},
    }
    dump_json(os.path.join(config.DATA_DIR, 'manifest.json'), manifest)
    print(f"data/ を書き出しました: events {len(events_list)} / rounds {len(publish_ctx)} / runs {len(all_runs)} / athletes {len(athletes)} / dataVersion {manifest['dataVersion']}")


def run_summary(run):
    return {'run_id': run['run_id'], 'athlete_id': run['athlete_id'], 'name': run['name'], 'noc': run.get('noc'), 'rank': run['rank'],
            'run_score': run['run_score'], 'time_points': run['time_points'], 'seconds': run['seconds'], 'air_total': run['air_total'],
            'turns_total': run['turns_total'], 'base_total': run['base_total'], 'ded_total': run['ded_total'],
            'air': [{'jump': a['jump'], 'dd': a['dd'], 'jump_score': a['jump_score']} for a in run['air']]}


if __name__ == '__main__':
    sys.exit(main())
