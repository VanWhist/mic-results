// ページ④ データについて。manifest と events から対象・検証・出典を組み立てる。
import * as data from './data.js';
import { el, clear, mountNav, errorBox, seriesLabel, LAYER_LABEL, TIER_LABEL, tierHelp, eventName, eventDates } from './ui.js';

async function main() {
  mountNav('about.html');
  let m, events, rules;
  try {
    m = await data.manifest();
    [events, rules] = await Promise.all([data.events(), data.rules()]);
  } catch (e) {
    document.getElementById('status').append(errorBox(e.message));
    return;
  }
  const st = clear(document.getElementById('status'));
  st.append(el('p', {}, [
    el('strong', { text: 'データ版 ' + m.dataVersion }), '（作成 ' + m.builtAt + '）　',
    '大会 ' + m.counts.events + ' 件 / ラウンド ' + m.counts.rounds + ' 件 / 記録 ' + m.counts.runs + ' 本 / 選手 ' + m.counts.athletes + ' 名',
  ]));
  st.append(el('p', { class: m.verification.allGreen ? 'verify-ok' : 'verify-ng',
    // 警告の件数（印字どおり載せた例外の注記など、作業用の記録の数）は出さない。読む人には誤りの数に見える
    text: m.verification.allGreen ? '✓ 公開中の全ラウンドが検証を通っています（PDF を目視して作った正解データ ' + m.verification.goldenRuns + ' 本とも一致）'
      : '⚠ 検証に未確認の層があるラウンドがあります' }));

  const tiers = clear(document.getElementById('tiers'));
  for (const t of ['detail', 'score', 'rank']) {
    tiers.append(el('li', {}, [el('strong', { text: TIER_LABEL[t] + '：' }), tierHelp(t)]));
  }

  const series = clear(document.getElementById('series'));
  const bySeries = new Map();
  for (const ev of events) {
    if (!bySeries.has(ev.series)) bySeries.set(ev.series, []);
    bySeries.get(ev.series).push(ev);
  }
  // 国内 → FIS 系 → W杯系の順（記号の ABC 順では並びの意味が分からなかった）
  const ORDER = ['SAJ_AJ', 'SAJ_AJJR', 'SAJ_A', 'SAJ_B', 'SAJ_KIDS', 'FIS', 'AC', 'ANC', 'NAC', 'EC', 'OPN', 'WJC', 'UVS', 'WC', 'WSC', 'OWG'];
  const rank = (s) => (ORDER.indexOf(s) < 0 ? ORDER.length : ORDER.indexOf(s));
  for (const [s, evs] of [...bySeries.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]))) {
    const seasons = [...new Set(evs.map((e) => e.season))].sort();
    const span = seasons[0] === seasons[seasons.length - 1] ? seasons[0] : seasons[0] + '〜' + seasons[seasons.length - 1];
    series.append(el('li', {}, [el('strong', { text: seriesLabel(s) }), '：' + evs.length + ' 大会（' + span + '）']));
  }

  const layers = clear(document.getElementById('layers'));
  for (const k of Object.keys(LAYER_LABEL)) layers.append(el('li', { text: LAYER_LABEL[k] }));

  const ruleBox = clear(document.getElementById('rules'));
  const describe = (r) => 'ターン ' + r.judges.turns + ' 名（' + (r.discard_high_low ? '最高・最低を除外' : '除外なし') + '、下限 ' + r.turns_floor + '）、エア ' + r.judges.air
    + ' 名（1人あたり上限 ' + r.air_cap_per_judge + '）、タイム点 ' + r.time_formula + '（0〜' + r.time_max + '）'
    + (r.tie_break && r.tie_break.length ? '、同点は FIS 式のタイブレーク' : '、同点は同順位');
  for (const [name, r] of Object.entries(rules || {})) {
    const desc = r && r.judges ? describe(r) : Object.values(r || {}).filter((x) => x && x.judges).map(describe).join('／');
    ruleBox.append(el('li', {}, [el('strong', { text: name + '：' }), desc, r && r.source ? el('span', { class: 'meta', text: '　根拠：' + r.source }) : null]));
  }

  // 古い about.html（折りたたみの見出しが無い）がキャッシュに残っていても、出典の一覧は出す
  const setText = (id, text) => { const n = document.getElementById(id); if (n) n.textContent = text; };
  setText('rules-summary', '規則の一覧を見る（' + Object.keys(rules || {}).length + ' 版）');
  setText('sources-summary', '出典の一覧を見る（' + events.length + ' 大会）');
  const src = clear(document.getElementById('sources'));
  for (const ev of events) {
    src.append(el('li', {}, [
      eventName(ev) + '（' + eventDates(ev) + '）：',
      // 結果の PDF が無く、FIS 公式サイトの結果ページから載せた大会は出典の PDF が無い
      (ev.sources || []).length ? null : 'FIS 公式サイトの結果ページ（結果の PDF なし）',
      ...(ev.sources || []).flatMap((s, i) => [i ? '、' : null,
        s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener', text: s.path }) : s.path,
        s.sha256 ? el('span', { class: 'meta', text: '　SHA-256 ' + s.sha256.slice(0, 12) + '…' }) : null]),
    ]));
  }
}

main();
