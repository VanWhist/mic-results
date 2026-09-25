// ページ③ 大会。event.html?id=<event_id>[#round_id]
// 大会情報 → ラウンドの切替 → 結果表。段階（tier）と審判構成（panel）に応じて列を変える。
import * as data from './data.js';
import {
  el, clear, cell, num, mountNav, errorBox, eventName, eventDates, seriesLabel, genderLabel, roundLabel, tierBadge,
  tierHelp, verificationBadge, layerMark, layerStatusLabel, LAYER_LABEL, statusBadge, reportLink, athleteHref, isNarrow,
  onWidthChange, queryParam, micBadge, noteLayers,
} from './ui.js';

let manifest = null;
let event = null;
let runsByRound = new Map();
let lines = new Map();
let athleteMap = new Map();
let current = null;   // 表示中の round

function roundKey(r) { return r.round_id; }

function renderHead() {
  const head = clear(document.getElementById('event-head'));
  head.append(el('h2', { text: eventName(event) }));
  const kv = el('div', { class: 'kv' });
  const add = (k, v) => { if (v) kv.append(el('div', {}, [el('strong', { text: k + '：' }), v])); };
  add('日付', eventDates(event));
  add('会場', event.venue);
  add('系列', (event.series_label || seriesLabel(event.series)) + (event.grade ? '（' + event.grade + '）' : ''));
  add('種目', event.discipline === 'DM' ? 'デュアルモーグル' : 'モーグル');
  add('進行', event.format_label);
  add('記録の段階', el('span', {}, (event.tiers || []).map((t) => tierBadge(t))));
  head.append(kv);
  const srcs = (event.sources || []);
  if (srcs.length) {
    // ラウンドごとに PDF がある大会（FIS 様式）は「男子 予選」のようにラベルを付け、同じ大会ページは1回だけ出す
    const roundsById = new Map((event.rounds || []).map((r) => [r.round_id, r]));
    const pdfLabel = (s) => {
      const rs = (s.round_ids || []).map((id) => roundsById.get(id)).filter(Boolean);
      return rs.length ? rs.map((r) => genderLabel(r.gender) + ' ' + roundLabel(r)).join('・') : '公式リザルト PDF';
    };
    const pages = [...new Set(srcs.map((s) => s.page_url).filter(Boolean))];
    head.append(el('p', { class: 'meta' }, ['出典：', ...srcs.flatMap((s, i) => [
      i ? '、' : null,
      s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener', text: pdfLabel(s) })
        : el('span', { text: (s.round_ids || []).length ? pdfLabel(s) + '（PDF の公開 URL なし）' : s.path }),
    ]), ...pages.flatMap((p) => ['（', el('a', { href: p, target: '_blank', rel: 'noopener', text: '大会ページ' }), '）'])]));
  }
}

function renderChips() {
  const box = clear(document.getElementById('round-chips'));
  const rounds = [...event.rounds];
  for (const r of rounds) {
    const b = el('button', { class: 'chip' + (current && current.round_id === r.round_id ? ' primary' : ''), type: 'button',
      text: genderLabel(r.gender) + ' ' + roundLabel(r) + '（' + (runsByRound.get(r.round_id) || []).length + '名）',
      onclick: () => { current = r; history.replaceState(null, '', '#' + encodeURIComponent(r.round_id)); renderChips(); renderRound(); } });
    box.append(b);
  }
}

function verificationBlock(r) {
  const badge = verificationBadge(r);
  const details = Object.entries(r.verification || {}).map(([k, v]) => {
    const m = layerMark(v);
    return el('div', { class: 'verify-line ' + m.cls, text: m.mark + (LAYER_LABEL[k] || k) + '：' + layerStatusLabel(v) });
  });
  const src = r.source || {};
  details.push(el('div', { class: 'meta' }, [
    '元 PDF：', src.url ? el('a', { href: src.url, target: '_blank', rel: 'noopener', text: src.pdf }) : src.pdf,
    src.pages ? '（' + src.pages.join(',') + ' ページ）' : null,
    '　規則：' + (src.rules_version || '—'),
    '　', reportLink({ round_id: r.round_id, dataVersion: manifest.dataVersion, pdf: src.pdf }),
  ]));
  details.push(el('div', { class: 'meta', text: tierHelp(r.tier) }));
  if (src.upstream && src.upstream.url) {
    details.push(el('div', { class: 'meta' }, [
      'このラウンドは、ナショナルチーム用サイトで審判ごとの点まで照合済みのデータを得点までの段階で写したものです。審判点は ',
      el('a', { href: src.upstream.url, target: '_blank', rel: 'noopener', text: 'モーグル リザルトデータベース' }), ' で見られます。']));
  }
  return noteLayers(badge.text, details);
}

function renderRound() {
  const r = current;
  const runs = (runsByRound.get(r.round_id) || []).slice().sort((a, b) => (a.rank || 999) - (b.rank || 999) || (a.bib || 0) - (b.bib || 0));
  const head = clear(document.getElementById('round-head'));
  head.append(el('div', { class: 'round-title' }, [
    el('h3', { text: genderLabel(r.gender) + ' ' + roundLabel(r) + '　' + (r.date || '') }),
    tierBadge(r.tier),
  ]));
  const kv = [];
  if (r.pace_time) kv.push('ペースタイム ' + num(r.pace_time) + ' 秒');
  if (r.panel) kv.push('審判 ターン ' + r.panel.turns + ' 名・エア ' + r.panel.air + ' 名');
  if (r.judges && r.judges.length) kv.push(r.judges.map((j) => 'J' + j.no + ' ' + j.name + (j.noc ? '（' + j.noc + '）' : '')).join('、'));
  const line = lines.get(r.round_id);
  if (line && line.cut) kv.push(line.cut.label + '：' + line.cut.run.name + ' ' + num(line.cut.run.run_score) + ' 点');
  head.append(el('p', { class: 'meta', text: kv.join('　／　') }));
  head.append(verificationBlock(r));
  const box = clear(document.getElementById('results'));
  if (!runs.length) { box.append(el('p', { class: 'meta', text: 'この記録はまだありません。' })); return; }
  if (isNarrow()) box.append(renderCards(r, runs));
  else box.append(renderTable(r, runs));
}

function nameCell(run) {
  const a = athleteMap.get(run.athlete_id);
  return el('td', {}, [el('a', { href: athleteHref(run.athlete_id), text: run.name }), ' ', micBadge(a), ...statusBadge(run)]);
}

function renderTable(r, runs) {
  const tier = r.tier;
  const nT = (r.panel && r.panel.turns) || 0;
  const airNos = (r.panel && r.panel.air_judge_nos) || [6, 7];
  const nJ = Math.max(1, ...runs.map((x) => (x.air || []).length));
  const th = (text, cls) => el('th', { class: cls || '', text });
  const heads = [th('順位'), th('Bib'), th('選手'), th('所属'), th('クラブ')];
  if (tier === 'detail') {
    heads.push(th('タイム', 'num'), th('タイム点', 'num'));
    for (let k = 0; k < nJ; k++) heads.push(th('ジャンプ' + (k + 1)), th('DD', 'num'), th('J' + airNos[0], 'num'), th('J' + airNos[1], 'num'));
    heads.push(th('エア', 'num'));
    for (let i = 1; i <= nT; i++) heads.push(th('B' + i, 'num'));
    for (let i = 1; i <= nT; i++) heads.push(th('D' + i, 'num'));
    heads.push(th('ターン', 'num'), th('スコア', 'num'), th('同点'));
  } else if (tier === 'score') {
    heads.push(th('タイム', 'num'), th('タイム点', 'num'), th('エア', 'num'), th('ターン', 'num'), th('スコア', 'num'));
  }
  const hasProg = runs.some((x) => x.components && x.components.progression);  // デュアルモーグル: 最終段と対戦経過
  if (hasProg) heads.push(th('最終段'), th('対戦経過'));
  const tbody = el('tbody');
  for (const run of runs) {
    const tds = [el('td', { class: 'num', text: run.rank ?? '—' }), el('td', { class: 'num', text: run.bib ?? '—' }), nameCell(run),
      el('td', { text: run.affiliation || '' }), el('td', { text: run.club || '' })];
    if (tier === 'detail') {
      tds.push(cell(run.seconds), cell(run.time_points));
      for (let k = 0; k < nJ; k++) {
        const a = (run.air || [])[k];
        tds.push(el('td', { text: a ? a.jump : '' }), cell(a ? a.dd : null, 2), cell(a ? a.J6 : null, 1), cell(a ? a.J7 : null, 1));
      }
      tds.push(cell(run.air_total));
      for (let i = 0; i < nT; i++) tds.push(cell(run.base[i], 1, (run.base_discard || []).includes(i) ? 'discard' : ''));
      for (let i = 0; i < nT; i++) tds.push(cell(run.ded[i], 1, (run.ded_discard || []).includes(i) ? 'discard' : ''));
      tds.push(cell(run.turns_total, 2, run.turns_floor_applied ? 'floor' : ''), cell(run.run_score), el('td', { text: run.tie || '' }));
    } else if (tier === 'score') {
      tds.push(cell(run.seconds), cell(run.time_points), cell(run.air_total), cell(run.turns_total), cell(run.run_score));
    }
    if (hasProg) {
      const c = run.components || {};
      tds.push(el('td', { text: c.stage || '' }), el('td', { class: 'prog', text: c.progression || '' }));
    }
    tbody.append(el('tr', {}, tds));
  }
  const table = el('table', {}, [el('thead', {}, el('tr', {}, heads)), tbody]);
  const legend = tier === 'detail' ? el('p', { class: 'meta', text:
    'B＝ベース点、D＝減点（マイナス）。' + (nT >= 5 ? '薄い字は最高・最低として除外された点。' : '3人制は除外なしで合計。') + 'ターンの下限は 0.3。' }) : null;
  return el('div', {}, [el('div', { class: 'table-wrap' }, table), legend]);
}

function renderCards(r, runs) {
  const box = el('div', { class: 'card-list' });
  for (const run of runs) {
    const a = athleteMap.get(run.athlete_id);
    const head = el('div', { class: 'rec-head' }, [
      el('span', { class: 'rec-rank', text: run.rank ? run.rank + '位' : (run.status || '—') }),
      el('a', { class: 'rec-name', href: athleteHref(run.athlete_id), text: run.name }), ' ', micBadge(a),
      el('span', { class: 'rec-total', text: run.run_score !== null && run.run_score !== undefined ? num(run.run_score) : '' }),
    ]);
    const sub = [run.affiliation, run.club].filter(Boolean).join(' ');
    const parts = [];
    if (run.components && run.components.progression) parts.push((run.components.stage ? run.components.stage + '　' : '') + run.components.progression);
    if (r.tier !== 'rank' && run.status === 'OK') {
      parts.push('タイム ' + num(run.seconds) + '秒（' + num(run.time_points) + '）');
      parts.push('エア ' + num(run.air_total) + (run.air && run.air.length ? '（' + run.air.map((x) => x.jump).join('・') + '）' : ''));
      parts.push('ターン ' + num(run.turns_total));
    }
    box.append(el('div', { class: 'rec' }, [head, el('div', { class: 'rec-sub', text: sub }), el('div', { class: 'rec-meta', text: parts.join('　') })]));
  }
  return box;
}

async function main() {
  mountNav('index.html');
  const id = queryParam('id');
  try {
    manifest = await data.manifest();
    const [events, lineList, athletes] = await Promise.all([data.events(), data.lines(), data.athletes()]);
    event = events.find((e) => e.event_id === id);
    if (!event) throw new Error('大会 ' + id + ' がありません');
    lines = new Map(lineList.map((l) => [l.round_id, l]));
    athleteMap = new Map(athletes.map((a) => [a.athlete_id, a]));
    const runs = await data.runs(event.season);
    for (const run of runs) {
      if (run.event_id !== event.event_id) continue;
      if (!runsByRound.has(run.round_id)) runsByRound.set(run.round_id, []);
      runsByRound.get(run.round_id).push(run);
    }
  } catch (e) {
    document.getElementById('event-head').append(errorBox(e.message));
    return;
  }
  document.title = eventName(event) + ' | MIC モーグル リザルト';
  renderHead();
  const want = decodeURIComponent(location.hash.slice(1));
  current = event.rounds.find((r) => r.round_id === want) || event.rounds.find((r) => r.round === 'F2') || event.rounds[event.rounds.length - 1];
  renderChips();
  renderRound();
  onWidthChange(() => renderRound());
}

main();
