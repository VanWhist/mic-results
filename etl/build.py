"""mic-results ETL: registry -> adapters -> 多層照合 -> data/*.json

    python -m etl.build [--accept-rounds] [--accept-revision] [--adapter saj_aj] [--event <substr>]

Every event comes from ``etl/registry/*.json``. Each registry entry names its adapter
(``etl/adapters/<name>/adapter.py`` with ``load_event(ev, imported_at, log)``), which returns round
contexts in the shared shape (see normalize.py). Verification and publication are tier-aware.
"""
import argparse, collections, datetime, glob, hashlib, importlib, json, os, pickle, re, sys, unicodedata

from . import config, normalize, verify, layer5_saj
from .adapters.saj_aj.parse_sajmo_old import PREFS

BUILD_VERSION = '2026-09-25-1'


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def dump_json(path, obj):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=0, separators=(',', ':'))


# ---- 大会ごとの読み取り結果のキャッシュ ------------------------------------------
# 全体ビルドは 1 時間以上かかり、途中で止められることがある。読み終わった大会の load_event の結果を保存し、
# やり直したときは読み込むだけにする。登録内容・ETL のプログラム・規則ファイル（moguls_results は上流の manifest）の
# どれかが変われば指紋が変わり、その大会は読み直す。保存先は etl/.build_cache/（Git に入れない）
CACHE_DIR = os.path.join(config.HERE, '.build_cache')


# アダプタが読み取りに使う他のアダプタ（import しているもの）。指紋にはこれらのファイルも入れる
ADAPTER_DEPS = {'saj_dm': ('saj_dm', 'saj_aj')}


def _code_fingerprint(adapter):
    """アダプタごとの指紋。共通部分（config・normalize・verify・scoring・規則ファイル）とそのアダプタ（と依存先）のファイル。
    FIS 海外大会用の fis_pdf を直しても SAJ の大会は読み直さない（2026-09-28 までは全アダプタで 1 つの指紋だった）"""
    h = hashlib.sha256()
    # 読み取り（load_event）に関わるファイルだけ。build.py・layer5_saj.py・tests を直してもキャッシュは使える
    core = [os.path.join(config.HERE, f) for f in ('config.py', 'normalize.py', 'verify.py', 'scoring.py') if os.path.exists(os.path.join(config.HERE, f))]
    # 登録を作り直すスクリプト（gen_registry.py・sync_registry.py）は読み取りに関わらないので除く
    adapters = [f for d in ADAPTER_DEPS.get(adapter, (adapter,))
                for f in glob.glob(os.path.join(config.HERE, 'adapters', d, '**', '*.py'), recursive=True)
                if os.path.basename(f) not in ('gen_registry.py', 'sync_registry.py')]
    adapters += [os.path.join(config.HERE, 'adapters', '__init__.py')]
    files = sorted(core + adapters + glob.glob(os.path.join(config.RULES_DIR, '**', '*.json'), recursive=True))
    for f in files:
        h.update(os.path.relpath(f, config.HERE).encode('utf-8'))
        with open(f, 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()


def _event_key(ev, code_fp):
    h = hashlib.sha256((code_fp + json.dumps(ev, ensure_ascii=False, sort_keys=True)).encode('utf-8'))
    if ev.get('adapter') == 'moguls_results':
        mf = os.path.join(config.MOGULS_RESULTS_REPO, 'data', 'manifest.json')
        if os.path.exists(mf):
            with open(mf, 'rb') as fh:
                h.update(fh.read())
    return h.hexdigest()[:24]


def load_event_cached(mod, ev, imported_at, code_fp, use_cache=True):
    path = os.path.join(CACHE_DIR, f"{ev['event_id']}.{_event_key(ev, code_fp)}.pkl")
    if use_cache and os.path.exists(path):
        try:
            with open(path, 'rb') as fh:
                return pickle.load(fh), True
        except Exception:  # noqa  壊れたキャッシュは読み直す
            pass
    ctxs = mod.load_event(ev, imported_at)
    os.makedirs(CACHE_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(CACHE_DIR, f"{ev['event_id']}.*.pkl")):
        os.remove(old)  # 同じ大会の古い指紋のキャッシュは消す
    tmp = path + '.tmp'
    with open(tmp, 'wb') as fh:
        pickle.dump(ctxs, fh)
    os.replace(tmp, path)
    return ctxs, False


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
            if ev.get('skip'):  # 対象外と決めた大会（理由は skip に書く）
                continue
            if adapter_filter and ev['adapter'] != adapter_filter:
                continue
            if event_filter and event_filter not in ev['event_id']:
                continue
            events.append(ev)
    return events


def apply_rank_exceptions(findings, round_id, ev):
    """registry の rank_exceptions（規則どおりなら順位が変わるが、印字の順位が公式と確認できたもの）に当たる
    layer3 の順位の不一致を、根拠つきの警告に下げる。登録したのに当たる不一致が無ければエラーにする（登録の見直し）"""
    excs = [e for e in ev.get('rank_exceptions') or [] if e.get('round_id') == round_id]
    if not excs:
        return findings
    out, used = [], set()
    for f in findings:
        if f.level == 'error' and f.layer == 'layer3':
            for i, e in enumerate(excs):
                if i not in used and f.message == f"{e['name']} 順位 印字 {e['printed']} / 再構成 {e['calc']}":
                    f = verify.Finding('warning', f.round_id, 'layer3', f"{f.message}（印字の順位を正とする例外。根拠: {e['basis']}）")
                    used.add(i)
                    break
        out.append(f)
    for i, e in enumerate(excs):
        if i not in used:
            out.append(verify.Finding('error', round_id, 'layer3',
                                      f"rank_exceptions の {e['name']}（印字 {e['printed']} / 再構成 {e['calc']}）に当たる順位の不一致が無い（登録を見直す）"))
    return out


def advance_for(ev, gender):
    fmt = ev.get('format') or {}
    adv = fmt.get('advance') or {}
    if gender in adv:
        return adv[gender]
    return adv


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--accept-rounds', action='store_true', help='新しいラウンドの人数を基準として登録する')
    ap.add_argument('--forget-rounds', action='append', default=[], metavar='SUBSTR',
                    help='round_id にこの文字列を含む基準を消す（大会・PDF を意図して登録から外したとき。--accept-rounds と一緒に使う）')
    ap.add_argument('--accept-revision', action='store_true', help='元PDFの変更（公式改訂）を受け入れて基準ハッシュを更新する')
    ap.add_argument('--adapter', default=None, help='このアダプタの大会だけ処理（開発用。公開ゲートは無効）')
    ap.add_argument('--event', default=None, help='event_id にこの文字列を含む大会だけ処理（開発用。公開ゲートは無効）')
    ap.add_argument('--no-cache', action='store_true', help='大会ごとの読み取り結果のキャッシュを使わずに全部読み直す')
    args = ap.parse_args(argv)
    dev = bool(args.adapter or args.event)

    expected = load_json(config.EXPECTED_ROUNDS, {})
    forgotten = sorted(k for k in expected if any(p in k for p in args.forget_rounds))
    for k in forgotten:
        del expected[k]
    if forgotten:
        print(f"基準から消した: {len(forgotten)} ラウンド（{', '.join(args.forget_rounds)}）")
    expected_before = set(expected)
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
    # 同じ PDF が 2 つ以上の大会に登録されていたら止める（SAJ データバンクで別レース番号に同じ PDF が付いていた
    # 2024 全日本ジュニア 0430/0431。同じ結果が別の大会として 2 回公開されるのを防ぐ）。同じ大会の男女に同じ PDF は可
    pdf_owner = {}
    for ev in events:
        for sha in {p.get('sha256') for p in ev.get('pdfs', []) if p.get('sha256')}:
            if sha in pdf_owner and pdf_owner[sha] != ev['event_id']:
                findings.append(verify.Finding('error', ev['event_id'], 'layer0',
                                               f"同じ PDF（SHA-256 {sha[:10]}…）が別の大会 {pdf_owner[sha]} にも登録されている"))
            pdf_owner.setdefault(sha, ev['event_id'])
    loaded = []  # (ev, ctx) を全部読んでから正規化する（SAJ 番号→FIS コードの対応を大会横断で使うため）
    code_fps = {}
    n_cached = 0
    for ev in events:
        mod = importlib.import_module(f"etl.adapters.{ev['adapter']}.adapter")
        try:
            if ev['adapter'] not in code_fps:
                code_fps[ev['adapter']] = _code_fingerprint(ev['adapter'])
            ctxs, hit = load_event_cached(mod, ev, imported_at, code_fps[ev['adapter']], use_cache=not args.no_cache)
            n_cached += hit
        except Exception as e:
            findings.append(verify.Finding('error', ev['event_id'], 'layer0', f"アダプタ例外: {e!r}"))
            continue
        for c in ctxs:
            if c.get('error_only'):
                findings.append(verify.Finding('error', c['event_id'], 'layer0', c['message']))
                continue
            loaded.append((ev, c))
    print(f"読み取り: {len(events)} 大会（うちキャッシュから {n_cached}）")
    unify_athlete_ids(loaded)
    merged_ids = apply_athlete_merges(loaded, master)
    for ev, c in loaded:
        if True:
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
                findings += apply_rank_exceptions(verify.layer3_rank(rnd['round_id'], runs, c['rules']), rnd['round_id'], ev)
            rounds_ctx.append(ctx)
            print(f"  読込 {rnd['round_id']:34s} {rnd['gender']} {rnd['round']:3s} {len(runs):3d} records  tier={rnd['tier']}")

    findings += verify.layer0(rounds_ctx, expected, args.accept_rounds, check_missing=not dev)
    n_detail = [ctx for ctx in rounds_ctx if ctx['round']['tier'] == 'detail']
    n_ab = sum(1 for ctx in n_detail if ctx.get('ab_compared'))
    if n_ab != len(n_detail):
        # 比較できなかったラウンドには個別のエラー（B 側の表が決まらない等）が付き、その大会は公開されない。
        # 全体を止めるほどではないので、件数は警告として残す
        findings.append(verify.Finding('warning', 'global', 'layer1', f"2方式比較が実行されたラウンド {n_ab}/{len(n_detail)}（残りは大会単位で非公開）"))
    findings += verify.dd_consistency(dd_seen)
    for ev in events:
        for gender in ('M', 'W'):
            # 総合の報告から組み立てた一部のラウンド（先へ進んだ選手の走りが無い。cls の partial）は勝ち上がりを検算できない
            rbc = {ctx['round']['round']: (ctx['round'], ctx['runs']) for ctx in rounds_ctx
                   if ctx['round']['event_id'] == ev['event_id'] and ctx['round']['gender'] == gender and ctx['round']['tier'] != 'rank'
                   and not ctx['cls'].get('partial')}
            if rbc:
                findings += verify.layer3_progression(ev['event_id'], rbc, advance_for(ev, gender))
    all_runs = [r for ctx in rounds_ctx for r in ctx['runs']]
    all_rounds = [ctx['round'] for ctx in rounds_ctx]
    findings += verify.layer4(all_runs, all_rounds, merged_ids)

    layer5_status = 'skipped'
    layer5_cache = load_json(config.LAYER5_CACHE, {})  # moguls-results から引き継いだ FIS 公式 Web との照合結果
    l5f, l5_saj = layer5_saj.cross_check(rounds_ctx, events_by_id)  # SAJ 競技データバンクの順位表との照合（キャッシュがある分だけ）
    findings += l5f
    n_l5 = sum(1 for v in l5_saj.values() if v != 'skipped')
    if n_l5 or layer5_cache:
        layer5_status = 'partial' if any(v == 'skipped' for v in l5_saj.values()) or len(l5_saj) + len(layer5_cache) < len(rounds_ctx) else 'ok'

    runs_by_id = {r['run_id']: r for r in all_runs}
    gf, n_golden, golden_rounds = verify.golden(config.GOLDEN_DIR, runs_by_id, strict=not dev)
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
            v['layer5'] = l5_saj.get(rid) or layer5_cache.get(rid, 'skipped')
        if v.get('golden') == 'ok' and rid not in golden_rounds:
            v['golden'] = 'skipped'  # このラウンドには正解データが無い
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

    if args.accept_rounds or forgotten:
        # 基準に登録するのは公開するラウンドだけ。検証を通らないラウンドの人数（読み違いかもしれない）は固定しない
        published_ids = {ctx['round']['round_id'] for ctx in publish_ctx}
        expected = {k: v for k, v in expected.items() if k in expected_before or k in published_ids}
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


def unify_athlete_ids(loaded):
    """SAJ 様式は予選に FIS コードが無く決勝にだけ印字される年がある。同じ SAJ 番号にどこかで FIS コードが印字されていれば、
    その選手の athlete_id を FIS コードに揃える（根拠は印字。同姓同名の推測はしない）。"""
    saj_to_fis = {}
    for _, c in loaded:
        for rec in c['records']:
            if rec.get('saj_no') and rec.get('fis_code'):
                saj_to_fis.setdefault(str(rec['saj_no']), str(rec['fis_code']))
    n = 0
    for _, c in loaded:
        for rec in c['records']:
            if rec.get('saj_no') and not rec.get('fis_code') and str(rec['saj_no']) in saj_to_fis:
                rec['fis_code'] = saj_to_fis[str(rec['saj_no'])]
                rec['athlete_id'] = rec['fis_code']
                n += 1
    if n:
        print(f"  SAJ 番号→FIS コードで athlete_id を揃えた記録: {n}")


def apply_athlete_merges(loaded, master):
    """athlete_master.json の merge_into で、別々の ID に分かれた同一人物を 1 つにまとめる（根拠を見て城さんが判断したものだけ）。
    名前由来の ID（x- で始まる）は別人と重なりうるので、names の氏名（空白を無視）に一致する記録だけを移す。
    印字の FIS コード・SAJ 番号・氏名は記録のまま残す。統合先の ID の集合を返す（第4層で FIS コードの複数を許すため）。"""
    merges = {aid: m for aid, m in master.get('athletes', {}).items() if m.get('merge_into')}
    chained = sorted(aid for aid, m in merges.items() if m['merge_into'] in merges)
    if chained:
        raise ValueError(f"athlete_master.json: 統合先がさらに統合されている {chained}")
    n = 0
    for _, c in loaded:
        for rec in c['records']:
            m = merges.get(rec.get('athlete_id'))
            if m and (not m.get('names') or _name_key(rec.get('name')) in {_name_key(x) for x in m['names']}):
                rec['athlete_id'] = m['merge_into']
                n += 1
    if n:
        print(f"  同一人物の統合（athlete_master.json）で athlete_id を移した記録: {n}")
    return {m['merge_into'] for m in merges.values()}


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


def _pref_glued(name, others):
    """name が別の表記 others のどれかに所属（都道府県名・半角カナのクラブ名）の文字が混ざっただけのものか。
    長い氏名が所属の欄にはみ出した PDF では、文字の拾い順で「松本 ベンジャミ千ン葉」「久保田 さくら愛知」になる。"""
    key = _name_key(name)
    for other in others:
        okey = _name_key(other)
        if not okey or okey == key:
            continue
        rest, i = [], 0
        for ch in key:  # okey が key の部分列なら、残りの文字を集める
            if i < len(okey) and ch == okey[i]:
                i += 1
            else:
                rest.append(ch)
        rest = ''.join(ch for ch in rest if not '｡' <= ch <= 'ﾟ').removesuffix('県')
        if i == len(okey) and rest in PREFS:
            return True
    return False


def display_name(names, foreign=False):
    """表示名: 日本語表記（SAJ 様式の印字）があればそれを優先し、無ければ最も多い表記。他の表記は別名になる。
    外国籍の選手の日本語表記がカナだけ（読みを写したもの。'パク センヨン'）なら、ローマ字の表記を選ぶ（漢字の名前は日本語表記のまま）。
    所属の文字が混ざった表記は、混ざっていない表記があればそちらを選ぶ。"""
    ja = collections.Counter({n: c for n, c in names.items() if verify.is_cjk(n)})
    latin = collections.Counter({n: c for n, c in names.items() if n not in ja})
    # 所属の漢字が混ざっただけの表記（'パク センヨン韓国'）は漢字の名前に数えない
    if foreign and latin and not any(re.search(r'[一-鿿]', n) for n in ja if not _pref_glued(n, ja)):
        pool = latin
    else:
        pool = ja or names
    clean = collections.Counter({n: c for n, c in pool.items() if not _pref_glued(n, pool)})
    return (clean or pool).most_common(1)[0][0]


def _aff_key(pref, club):
    """所属の履歴で同じ所属とみなすためのキー (都道府県, クラブ)。空白・中黒・全角半角・大文字小文字と、都道府県名の末尾の
    「都・府・県」を無視する（'東京都 / ﾁｰﾑ ｼﾞｮｯｸｽ' と '東京 / ﾁｰﾑｼﾞｮｯｸｽ' は同じ）。所属の欄が都道府県名でない印字は
    クラブ名が所属の欄にずれている（'ﾁｰﾑ / ｽﾉｰｱﾐｭｰｽﾞﾒﾝﾄ'）ので、2 つをつないだものをクラブとし、都道府県は不明（None）。"""
    def norm(s):
        return re.sub(r'[\s・･]', '', unicodedata.normalize('NFKC', s or '')).lower()
    p, c = norm(pref), norm(club)
    if p[:-1] in PREFS and p[-1:] in ('都', '府', '県'):
        p = p[:-1]
    if p and p not in PREFS:
        return None, p + c
    return p or None, c


def affiliation_history(rs_sorted):
    """選手ページの所属の履歴。印字の表記ゆれ（_aff_key）は同じ所属としてまとめ、都道府県が読めた表記のうちいちばん多いものを出す。
    FIS 様式（所属の印字なし）は飛ばす。クラブ名の欄が空の印字は、同じシーズンに同じ都道府県のクラブ名つきの印字があれば
    情報が無いので飛ばす（無ければ都道府県だけの行として残す）。記録（runs）の所属・クラブは印字のまま。"""
    keys = [(x, _aff_key(x.get('affiliation'), x.get('club'))) for x in rs_sorted]
    with_club = {(x['season'], p) for x, (p, c) in keys if c}
    hist = []
    for x, (pref, club) in keys:
        if not club and (pref is None or (x['season'], pref) in with_club):
            continue
        last = hist[-1] if hist else None
        if not last or last['club'] != club or (pref and last['pref'] and pref != last['pref']):
            last = {'club': club, 'pref': pref, 'forms': collections.Counter(), 'from': x['season']}
            hist.append(last)
        last['pref'] = last['pref'] or pref
        last['to'] = x['season']
        last['forms'][(x.get('affiliation'), x.get('club'), pref is not None)] += 1
    out = []
    for h in hist:
        forms = [f for f in h['forms'].most_common() if f[0][2]] or h['forms'].most_common()
        aff, club, _ = forms[0][0]
        out.append({'affiliation': aff, 'club': club, 'from': h['from'], 'to': h['to']})
    return out


def is_saj_no(value, fis_code):
    """選手ページに SAJ 番号として出してよい値か。外国籍の選手の SAJ 番号の欄には、仮の番号（2019 田沢湖などの 9999999、
    2017 全日本 DM の 5000000）や FIS コード（全日本 2024 MOON SEOYOUNG）が印字されることがある。記録（runs）の印字はそのまま残す。"""
    return bool(value) and str(value) != str(fis_code) and not re.fullmatch(r'9999\d{3}|5000000', str(value))


def _name_key(name):
    return ''.join((name or '').split())


def mic_status(roster, athlete_id, name, aliases=()):
    """名簿は athlete_id か氏名（空白を無視、別名も含む）で照合する。"""
    keys = {_name_key(name)} | {_name_key(a) for a in aliases}
    keys.discard('')
    for m in roster.get('members', []):
        if (m.get('athlete_id') and m.get('athlete_id') == athlete_id) or _name_key(m.get('name')) in keys:
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
            'venue': r['venue'], 'nation': ev.get('nation') or ('JPN' if ev['series'].startswith('SAJ') else None),
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
    merge_targets = {m['merge_into'] for m in master.get('athletes', {}).values() if m.get('merge_into')}
    for aid, rs in by_id.items():
        rs_sorted = sorted(rs, key=lambda x: x['date'] or '')
        names = collections.Counter(x['name'] for x in rs)
        name = display_name(names, foreign=any(x.get('noc') not in (None, 'JPN') for x in rs))
        aff_hist = affiliation_history(rs_sorted)
        al = aliases.get(aid, {}) if isinstance(aliases, dict) else {}
        alias_list = [n for n in names if n != name] + ([al.get('kana')] if al.get('kana') else []) + list(al.get('kanji', []))
        best = max([x for x in rs if x['run_score'] is not None], key=lambda x: x['run_score'], default=None)
        # 統合した選手は印字の番号が複数あるので、FIS コードは統合先の ID（FIS に残っている登録）、SAJ 番号はいちばん新しい印字を代表にする
        fis = aid if aid.isdigit() else next((x['fis_code'] for x in rs_sorted if x.get('fis_code')), None)
        saj = next((x['saj_no'] for x in (reversed(rs_sorted) if aid in merge_targets else rs_sorted) if is_saj_no(x.get('saj_no'), fis)), None)
        athletes.append({'athlete_id': aid, 'fis_code': fis, 'saj_no': saj, 'name': name, 'aliases': [a for a in alias_list if a],
                         'noc': rs_sorted[-1].get('noc'), 'yb': next((x['yb'] for x in reversed(rs_sorted) if x.get('yb')), None),
                         # 所属は FIS 様式には印字されないので、最後に印字があった大会の値を使う
                         'affiliation': next((x['affiliation'] for x in reversed(rs_sorted) if x.get('affiliation')), None),
                         'club': next((x['club'] for x in reversed(rs_sorted) if x.get('club')), None),
                         'affiliation_history': aff_hist, 'mic': mic_status(roster, aid, name, [a for a in alias_list if a]),
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
