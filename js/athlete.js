// ページ② 選手。athlete.html?id=<athlete_id>
// キャリア年表（系列をまたいで新しい順）、得点の推移、自己ベスト、ジャンプ構成。
import * as data from './data.js';
import {
  el, clear, num, mountNav, errorBox, eventShortName, genderLabel, roundLabel, tierBadge, TIER_SHORT, athleteMatches,
  athleteHref, eventHref, micBadge, queryParam, statusBadge,
} from './ui.js';

let athletes = [];
let runs = [];
let rounds = new Map();   // round_id → { event, round }
let manifest = null;

function renderSearch(query) {
  const box = clear(document.getElementById('results'));
  const list = query ? athletes.filter((a) => athleteMatches(a, query)) : athletes.filter((a) => a.mic);
  if (!query) box.append(el('p', { class: 'meta', text: list.length ? 'MIC の選手' : '選手名を入力してください（部分一致）。' }));
  if (query && !list.length) { box.append(el('p', { class: 'meta', text: '該当する選手がいません。' })); return; }
  if (query) box.append(el('p', { class: 'meta', text: list.length + ' 名' }));
  const tbody = el('tbody');
  for (const a of list.slice(0, 200)) {
    tbody.append(el('tr', {}, [
      el('td', {}, [el('a', { href: athleteHref(a.athlete_id), text: a.name }), ' ', micBadge(a)]),
      el('td', { text: [a.affiliation, a.club].filter(Boolean).join(' ') }),
      el('td', { text: (a.aliases || []).join('、') }),
      el('td', { class: 'num', text: a.n_results }),
      el('td', { class: 'num', text: a.best ? num(a.best.run_score) : '—' }),
      el('td', { text: (a.seasons || []).slice(-1)[0] || '' }),
    ]));
  }
  box.append(el('div', { class: 'table-wrap' }, el('table', {}, [
    el('thead', {}, el('tr', {}, ['選手', '所属', '別名', '記録', '自己ベスト', '最新シーズン'].map((t) => el('th', { class: 'no-sort', text: t })))),
    tbody,
  ])));
}

function renderProfile(a) {
  const box = clear(document.getElementById('profile'));
  box.append(el('h2', {}, [a.name, ' ', micBadge(a)]));
  const kv = el('div', { class: 'kv' });
  const add = (k, v) => { if (v) kv.append(el('div', {}, [el('strong', { text: k + '：' }), v])); };
  add('所属', [a.affiliation, a.club].filter(Boolean).join(' '));
  add('別名', (a.aliases || []).join('、'));
  add('FIS コード', a.fis_code);
  add('SAJ 番号', a.saj_no);
  add('生年', a.yb ? String(a.yb) : null);
  add('出場シーズン', (a.seasons || []).join('、'));
  if (a.best) {
    const ctx = rounds.get(a.best.run_id.replace(/-[^-]+$/, ''));
    add('自己ベスト', num(a.best.run_score) + ' 点' + (ctx ? '（' + eventShortName(ctx.event) + ' ' + genderLabel(ctx.round.gender) + ' ' + roundLabel(ctx.round) + '）' : ''));
  }
  const hist = (a.affiliation_history || []);
  if (hist.length > 1) add('所属の履歴', hist.map((h) => [h.affiliation, h.club].filter(Boolean).join(' ') + '（' + h.from + (h.to !== h.from ? '〜' + h.to : '') + '）').join('、'));
  box.append(kv);
}

function myRuns(a) {
  return runs.filter((r) => r.athlete_id === a.athlete_id && r.counting !== false)
    .map((r) => ({ run: r, ctx: rounds.get(r.round_id) }))
    .filter((x) => x.ctx)
    .sort((x, y) => (y.run.date || '').localeCompare(x.run.date || '') || (y.ctx.event.event_id).localeCompare(x.ctx.event.event_id)
      || ['Q', 'Q1', 'Q2', 'F1', 'F2', 'F3'].indexOf(y.run.round) - ['Q', 'Q1', 'Q2', 'F1', 'F2', 'F3'].indexOf(x.run.round));
}

function renderHistory(a) {
  const box = clear(document.getElementById('history'));
  const list = myRuns(a);
  if (!list.length) { box.append(el('p', { class: 'meta', text: '記録がありません。' })); return; }
  let season = null;
  const tbody = el('tbody');
  for (const { run, ctx } of list) {
    if (run.season !== season) {
      season = run.season;
      tbody.append(el('tr', { class: 'season-row' }, el('td', { colspan: 9, text: season + ' シーズン' })));
    }
    const n = ctx.roundSize || '';
    tbody.append(el('tr', {}, [
      el('td', { text: run.date || '' }),
      el('td', {}, el('a', { href: eventHref(ctx.event.event_id, run.round_id), text: eventShortName(ctx.event) + (ctx.event.name_ja ? '' : '') })),
      el('td', { text: ctx.event.venue || '' }),
      el('td', { text: genderLabel(run.gender) + ' ' + roundLabel(ctx.round) }),
      el('td', { class: 'num' }, [run.rank ? run.rank + ' / ' + n : '—', ...statusBadge(run)]),
      el('td', { class: 'num', text: run.run_score !== null && run.run_score !== undefined ? num(run.run_score) : '—' }),
      el('td', { class: 'num', text: run.tier === 'detail' && run.status === 'OK' ? num(run.turns_total) + ' / ' + num(run.air_total) + ' / ' + num(run.time_points) : '' }),
      el('td', { text: (run.air || []).map((x) => x.jump).join('・') }),
      el('td', {}, el('span', { class: 'badge tier tier-' + run.tier, text: TIER_SHORT[run.tier] || run.tier })),
    ]));
  }
  box.append(el('div', { class: 'table-wrap' }, el('table', { class: 'timeline' }, [
    el('thead', {}, el('tr', {}, ['日付', '大会', '会場', 'ラウンド', '順位 / 人数', '得点', 'ターン / エア / タイム点', 'ジャンプ', '記録'].map((t) => el('th', { class: 'no-sort', text: t })))),
    tbody,
  ])));
}

// 得点の推移。SVG を手で描く（ライブラリなし）。系列グループで色を分ける。
function renderChart(a) {
  const box = clear(document.getElementById('chart'));
  const pts = myRuns(a).filter(({ run }) => run.status === 'OK' && run.run_score !== null && run.run_score !== undefined && run.tier !== 'rank')
    .map(({ run, ctx }) => ({ date: run.date, score: run.run_score, group: ctx.event.series_group, label: eventShortName(ctx.event) + ' ' + roundLabel(ctx.round), href: eventHref(ctx.event.event_id, run.round_id) }))
    .sort((x, y) => (x.date || '').localeCompare(y.date || ''));
  if (pts.length < 2) { box.append(el('p', { class: 'meta', text: '得点の記録が2本以上になるとグラフを出します。' })); return; }
  const W = 720, H = 240, L = 44, R = 12, T = 12, B = 36;
  const t0 = new Date(pts[0].date).getTime(), t1 = new Date(pts[pts.length - 1].date).getTime() || t0 + 1;
  const ys = pts.map((p) => p.score);
  const y0 = Math.floor(Math.min(...ys) / 10) * 10, y1 = Math.ceil(Math.max(...ys) / 10) * 10 || y0 + 10;
  const sx = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const sy = (v) => T + (1 - (v - y0) / Math.max(1, y1 - y0)) * (H - T - B);
  const color = { 'W杯系': '#b45309', 'FIS系': '#0f7b4f', '国内': '#1f6feb' };
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('class', 'chart');
  const mk = (tag, attrs, text) => { const n = document.createElementNS(svgNS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (text !== undefined) n.textContent = text; return n; };
  for (let v = y0; v <= y1; v += 10) {
    svg.append(mk('line', { x1: L, x2: W - R, y1: sy(v), y2: sy(v), class: 'grid' }));
    svg.append(mk('text', { x: L - 6, y: sy(v) + 4, class: 'axis', 'text-anchor': 'end' }, String(v)));
  }
  const years = [...new Set(pts.map((p) => (p.date || '').slice(0, 4)))];
  for (const y of years) {
    const t = new Date(y + '-01-01').getTime();
    if (t >= t0 && t <= t1) svg.append(mk('text', { x: sx(t), y: H - 12, class: 'axis', 'text-anchor': 'middle' }, y));
  }
  svg.append(mk('polyline', { points: pts.map((p) => sx(new Date(p.date).getTime()) + ',' + sy(p.score)).join(' '), class: 'line' }));
  for (const p of pts) {
    const c = mk('circle', { cx: sx(new Date(p.date).getTime()), cy: sy(p.score), r: 4, fill: color[p.group] || '#1f6feb' });
    c.append(mk('title', {}, p.date + ' ' + p.label + '　' + num(p.score)));
    const a = mk('a', { href: p.href });
    a.append(c);
    svg.append(a);
  }
  box.append(svg);
  box.append(el('p', { class: 'meta', text: '点は1本の滑走。色：青＝国内、緑＝FIS系、橙＝W杯系。点を押すとその大会へ移動します。' }));
}

function renderJumps(a) {
  const box = clear(document.getElementById('jumps'));
  const list = myRuns(a).filter(({ run }) => run.tier === 'detail' && run.status === 'OK' && (run.air || []).length);
  if (!list.length) { box.append(el('p', { class: 'meta', text: 'ジャッジ明細のある記録がまだありません。' })); return; }
  const tbody = el('tbody');
  for (const { run, ctx } of list) {
    tbody.append(el('tr', {}, [
      el('td', { text: run.date || '' }),
      el('td', {}, el('a', { href: eventHref(ctx.event.event_id, run.round_id), text: eventShortName(ctx.event) + ' ' + roundLabel(ctx.round) })),
      ...[0, 1].flatMap((k) => { const j = run.air[k]; return [el('td', { text: j ? j.jump : '' }), el('td', { class: 'num', text: j ? num(j.dd, 2) : '' }), el('td', { class: 'num', text: j ? num((j.J6 + j.J7) / 2, 2) : '' })]; }),
      el('td', { class: 'num', text: num(run.air_total) }),
    ]));
  }
  box.append(el('div', { class: 'table-wrap' }, el('table', {}, [
    el('thead', {}, el('tr', {}, ['日付', '大会', '1本目', 'DD', '実施点', '2本目', 'DD', '実施点', 'エア合計'].map((t) => el('th', { class: 'no-sort', text: t })))),
    tbody,
  ])));
  box.append(el('p', { class: 'meta', text: '実施点はエアジャッジ2人の平均（DD を掛ける前）。' }));
}

async function main() {
  mountNav('athlete.html');
  try {
    manifest = await data.manifest();
    athletes = await data.athletes();
  } catch (e) {
    document.getElementById('results').append(errorBox(e.message));
    return;
  }
  const id = queryParam('id');
  if (!id) {
    const q = document.getElementById('q');
    q.addEventListener('input', () => renderSearch(q.value.trim()));
    renderSearch('');
    return;
  }
  const a = athletes.find((x) => x.athlete_id === id);
  if (!a) { document.getElementById('results').append(errorBox('選手 ' + id + ' がありません')); return; }
  document.getElementById('search-card').hidden = true;
  try {
    const [events, all] = await Promise.all([data.events(), data.allRuns()]);
    runs = all;
    const sizes = new Map();
    for (const r of runs) sizes.set(r.round_id, (sizes.get(r.round_id) || 0) + (r.counting === false ? 0 : 1));
    for (const ev of events) for (const r of ev.rounds || []) rounds.set(r.round_id, { event: ev, round: r, roundSize: sizes.get(r.round_id) || 0 });
  } catch (e) {
    document.getElementById('profile').append(errorBox(e.message));
    return;
  }
  document.title = a.name + ' | MIC モーグル リザルト';
  for (const idc of ['athlete-card', 'history-card', 'chart-card', 'jumps-card']) document.getElementById(idc).hidden = false;
  renderProfile(a);
  renderHistory(a);
  renderChart(a);
  renderJumps(a);
}

main();
