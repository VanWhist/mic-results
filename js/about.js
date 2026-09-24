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
    text: m.verification.allGreen ? '✓ 公開中の全ラウンドが検証を通っています（正解データ ' + m.verification.goldenRuns + ' 本、警告 ' + m.verification.warnings + ' 件）'
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
  for (const [s, evs] of [...bySeries.entries()].sort()) {
    const seasons = [...new Set(evs.map((e) => e.season))].sort();
    series.append(el('li', {}, [el('strong', { text: seriesLabel(s) }), '：' + evs.length + ' 大会（' + seasons[0] + '〜' + seasons[seasons.length - 1] + '）']));
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

  const src = clear(document.getElementById('sources'));
  for (const ev of events) {
    src.append(el('li', {}, [
      eventName(ev) + '（' + eventDates(ev) + '）：',
      ...(ev.sources || []).flatMap((s, i) => [i ? '、' : null,
        s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener', text: s.path }) : s.path,
        s.sha256 ? el('span', { class: 'meta', text: '　SHA-256 ' + s.sha256.slice(0, 12) + '…' }) : null]),
    ]));
  }
}

main();
