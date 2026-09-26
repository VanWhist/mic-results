// ページ③ 大会。event.html?id=<event_id>[#round_id]
// 大会情報 → ラウンドの切替 → 結果表。段階（tier）と審判構成（panel）に応じて列を変える。
import * as data from './data.js';
import {
  el, clear, cell, num, mountNav, errorBox, eventName, eventDates, seriesLabel, genderLabel, roundLabel, tierBadge,
  tierHelp, verificationBadge, layerMark, layerStatusLabel, LAYER_LABEL, statusBadge, reportLink, athleteHref, isNarrow,
  onWidthChange, queryParam, noteLayers,
} from './ui.js';

let manifest = null;
let event = null;
let runsByRound = new Map();
let lines = new Map();
let current = null;   // 表示中の round

// 結果表の見た目（ナショナルチーム用の moguls-results と同じ）。'fis' = 公式 PDF と同じ3行ブロック（既定）、
// 'table' = 1ラン1行の表。localStorage が使えない環境でも既定で表示できるように try/catch で包む。
const VIEW_KEY = 'mic-results.view';
let view = 'fis';
function loadView() {
  try { return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'fis'; } catch (e) { return 'fis'; }
}
function saveView(v) {
  try { localStorage.setItem(VIEW_KEY, v); } catch (e) { /* 覚えられなくても表示は切り替える */ }
}

function roundKey(r) { return r.round_id; }

function renderHead() {
  const head = clear(document.getElementById('event-head'));
  head.append(el('h2', { text: eventName(event) }));
  // スマホで結果までの距離を縮めるため、大会情報は 2 行に詰め、進行・出典は折りたたむ
  head.append(el('p', { class: 'ev-line', text: [eventDates(event), event.venue].filter(Boolean).join('　') }));
  head.append(el('p', { class: 'ev-line' }, [
    // 「全日本選手権（全日本）」のように系列名に級が含まれるときは重ねない
    (event.series_label || seriesLabel(event.series))
      + (event.grade && !(event.series_label || '').includes(event.grade) ? '（' + event.grade + '）' : ''),
    '　' + (event.discipline === 'DM' ? 'デュアルモーグル' : 'モーグル') + '　',
    ...(event.tiers || []).map((t) => tierBadge(t)),
  ]));
  const more = [];
  if (event.format_label) more.push(el('p', { class: 'meta', text: '進行：' + event.format_label }));
  const srcs = (event.sources || []);
  if (srcs.length) {
    // ラウンドごとに PDF がある大会（FIS 様式）は「男子 予選」のようにラベルを付け、同じ大会ページは1回だけ出す
    const roundsById = new Map((event.rounds || []).map((r) => [r.round_id, r]));
    const pdfLabel = (s) => {
      const rs = (s.round_ids || []).map((id) => roundsById.get(id)).filter(Boolean);
      return rs.length ? rs.map((r) => genderLabel(r.gender) + ' ' + roundLabel(r)).join('・') : '公式リザルト PDF';
    };
    const pages = [...new Set(srcs.map((s) => s.page_url).filter(Boolean))];
    more.push(el('p', { class: 'meta' }, ['出典：', ...srcs.flatMap((s, i) => [
      i ? '、' : null,
      s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener', text: pdfLabel(s) })
        : el('span', { text: (s.round_ids || []).length ? pdfLabel(s) + '（PDF の公開 URL なし）' : s.path }),
    ]), ...pages.flatMap((p) => ['（', el('a', { href: p, target: '_blank', rel: 'noopener', text: '大会ページ' }), '）'])]));
  }
  if (more.length) head.append(el('details', { class: 'ev-more' }, [el('summary', { text: '大会情報・出典を見る' }), ...more]));
}

function renderChips() {
  const box = clear(document.getElementById('round-chips'));
  const rounds = [...event.rounds];
  for (const r of rounds) {
    const b = el('button', { class: 'chip' + (current && current.round_id === r.round_id ? ' primary' : ''), type: 'button',
      text: genderLabel(r.gender) + ' ' + roundLabel(r) + '（' + (runsByRound.get(r.round_id) || []).length + '名）',
      onclick: () => {
        current = r; history.replaceState(null, '', '#' + encodeURIComponent(r.round_id)); renderChips(); renderRound();
        // スクロールの途中（チップが上に固定されている状態）で切り替えたら、新しいラウンドの頭へ戻す
        const top = document.getElementById('round-head');
        if (top.getBoundingClientRect().top < 0) top.scrollIntoView({ block: 'start' });
      } });
    box.append(b);
  }
}

function verificationBlock(r) {
  const badge = verificationBadge(r);
  const details = [];
  const judges = [];
  if (r.panel) judges.push('ターン ' + r.panel.turns + ' 名・エア ' + r.panel.air + ' 名');
  if (r.judges && r.judges.length) judges.push(r.judges.map((j) => 'J' + j.no + ' ' + j.name + (j.noc ? '（' + j.noc + '）' : '')).join('、'));
  if (judges.length) details.push(el('div', { class: 'meta', text: '審判：' + judges.join('　') }));
  details.push(...Object.entries(r.verification || {}).map(([k, v]) => {
    const m = layerMark(v);
    return el('div', { class: 'verify-line ' + m.cls, text: m.mark + (LAYER_LABEL[k] || k) + '：' + layerStatusLabel(v) });
  }));
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
  const line = lines.get(r.round_id);
  if (line && line.cut) kv.push(line.cut.label + '：' + line.cut.run.name + ' ' + num(line.cut.run.run_score) + ' 点');
  if (kv.length) head.append(el('p', { class: 'meta', text: kv.join('　／　') }));
  head.append(verificationBlock(r));
  const box = clear(document.getElementById('results'));
  // 審判点・得点のある段階だけ FIS 形式にできる。順位のみ・デュアルモーグルは従来の表
  const fisCapable = r.tier !== 'rank' && !runs.some((x) => x.components && x.components.progression);
  document.getElementById('view-toggle').hidden = !fisCapable || isNarrow();
  if (!runs.length) { box.append(el('p', { class: 'meta', text: 'この記録はまだありません。' })); return; }
  if (!fisCapable) box.append(isNarrow() ? renderCards(r, runs) : renderTable(r, runs));
  else if (isNarrow()) box.append(renderFisCards(r, orderedRuns(runs)));
  else box.append(view === 'table' ? renderTable(r, runs) : renderFis(r, orderedRuns(runs)));
}

// ---- FIS 公式リザルト PDF と同じ並びの表（moguls-results の index.js と同じ形） -------------
// 1選手 = 3行。1行目: 順位〜所属・タイム・エア1・B: ベース点・Run Score、
// 2行目: エア2・D: 減点、3行目: タイム点・エア合計・ターン合計（罫線の下に太字）。
// 審判の人数は大会ごと（ターン 3 名 / 5 名、エア審判の番号）なので round.panel から列を作る。
// 得点までの段階（score）はブロックの中身が合計だけなので、1選手1行にする。
// Q2 の PDF（q_block のある WC など）は選手ごとに Q2 → Q1 の走りを積む。DNF/DNS/DSQ は識別列と状態のみ。

const isQ1Block = (r) => r.q_block === 'Q1';

// 表示順に並べる。Q2 のあるラウンドは選手ごとに Q2 → Q1参考 の2段
function orderedRuns(runs) {
  const byRank = (a, b) => {
    const an = a.rank === null || a.rank === undefined, bn = b.rank === null || b.rank === undefined;
    if (an && bn) return (a.bib || 0) - (b.bib || 0);
    if (an) return 1;
    if (bn) return -1;
    return a.rank - b.rank || (a.bib || 0) - (b.bib || 0);
  };
  if (!runs.some((r) => r.q_block)) return runs.slice().sort(byRank).map((r) => ({ run: r, role: null, pairTop: false }));
  const groups = new Map();
  for (const r of runs) {
    if (!groups.has(r.athlete_id)) groups.set(r.athlete_id, []);
    groups.get(r.athlete_id).push(r);
  }
  const heads = [...groups.values()].map((g) => g.find((r) => r.q_block === 'Q2') || g[0]).sort(byRank);
  const out = [];
  for (const h of heads) {
    const g = groups.get(h.athlete_id);
    const q2 = g.find((r) => r.q_block === 'Q2');
    const q1 = g.find((r) => isQ1Block(r));
    if (q2) out.push({ run: q2, role: 'Q2', pairTop: !!q1 });
    if (q1) out.push({ run: q1, role: q2 ? 'Q1ref' : 'Q1', pairTop: false });
  }
  return out;
}

// orderedRuns の並び（Q2 → Q1参考）を選手単位にまとめる
function fisBlocks(items) {
  const out = [];
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const next = items[i + 1];
    if (it.pairTop && next && next.role === 'Q1ref') {
      out.push([it, next]);
      i++;
    } else {
      out.push([it]);
    }
  }
  return out;
}

// ラウンドの列構成。FIS の大会（NOC・生年のある行）は FIS Code / NSA Code / YB、国内大会は 所属 / クラブ
function fisLayout(r, items) {
  const rows = items.map((it) => it.run);
  const intl = rows.some((x) => x.noc);
  return {
    detail: r.tier === 'detail',
    nT: (r.panel && r.panel.turns) || 5,
    airNos: (r.panel && r.panel.air_judge_nos) || [6, 7],
    qLayout: items.some((it) => !!it.role),
    intl,
    showClub: !intl && rows.some((x) => x.club),
  };
}

const fisBlanks = (n) => Array.from({ length: n }, () => el('td'));

function fisNum(v, digits, cls = '') {
  const text = num(v, digits);
  return el('td', { class: ('num ' + cls).trim(), text: text === null ? '' : text });
}

function fisJudgeCells(values, discard, n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const dropped = (discard || []).includes(i);
    out.push(el('td', {
      class: 'num' + (dropped ? ' discard' : ''),
      title: dropped ? '最高・最低のため除外' : null,
      text: num(values && values[i], 1) ?? '',
    }));
  }
  return out;
}

function fisAirCells(air, idx) {
  const a = (air || [])[idx] || {};
  return [fisNum(a.J6, 1), fisNum(a.J7, 1), el('td', { class: 'jump', text: a.jump || '' }), fisNum(a.dd, 2)];
}

function turnsText(r) {
  return (num(r.turns_total, 1) ?? '') + (r.turns_floor_applied ? '*' : '');
}

function idCount(L) { return (L.intl ? 6 : 4 + (L.showClub ? 1 : 0)) + (L.qLayout ? 1 : 0); }

// 識別列。Q1 参考ブロックはラベルだけ出す
function fisIdentityCells(item, L) {
  const r = item.run;
  const head = item.role !== 'Q1ref';
  const cells = [
    el('td', { class: 'num bold', text: head ? (r.rank ?? '') : '' }),
    el('td', { class: 'num', text: head ? (r.bib ?? '') : '' }),
  ];
  if (L.intl) cells.push(el('td', { class: 'num', text: head ? (r.fis_code || '') : '' }));
  cells.push(el('td', { class: 'name' }, head ? [el('a', { href: athleteHref(r.athlete_id), text: r.name })] : []));
  cells.push(el('td', { text: head ? (L.intl ? (r.noc || '') : (r.affiliation || '')) : '' }));
  if (L.intl) cells.push(el('td', { class: 'num', text: head ? (r.yb ?? '') : '' }));
  else if (L.showClub) cells.push(el('td', { class: 'club', text: head ? (r.club || '') : '' }));
  if (L.qLayout) cells.push(el('td', { class: 'qlab', text: item.role === 'Q1ref' ? 'Q1' : (item.role || '') }));
  return cells;
}

// Run Score・Best Score・Tie（1行目のみ）。PDF は RES を Tie の位置に印字する
function fisScoreCells(item, L) {
  const r = item.run;
  const dnf = r.status && r.status !== 'OK';
  const score = el('td', { class: 'num bold score', text: dnf ? r.status : (num(r.run_score, 2) ?? '') });
  const best = L.qLayout ? [fisNum(item.role === 'Q1ref' ? null : r.best_score, 2, 'bold')] : [];
  const tie = el('td', { class: 'num tie' }, [
    r.tie ?? '',
    r.reserve_judge ? el('span', { class: 'fis-res', text: 'RES', title: 'リザーブジャッジが採点したラン（PDF の RES 印）' }) : null,
  ]);
  return [score, ...best, tie];
}

function fisRows(item, L) {
  const r = item.run;
  const scoreCount = 2 + (L.qLayout ? 1 : 0);
  const sub = item.role === 'Q1ref' ? ' fis-sub' : '';
  const nMid = L.detail ? 2 + 5 + (L.nT + 2) : 4;
  if (r.status && r.status !== 'OK' && r.seconds == null && r.air_total == null && r.turns_total == null) {
    return [el('tr', { class: 'fis-l1 fis-status' + sub }, [
      ...fisIdentityCells(item, L), ...fisBlanks(nMid), ...fisScoreCells(item, L),
    ])];
  }
  if (!L.detail) {
    return [el('tr', { class: 'fis-l1' + sub }, [
      ...fisIdentityCells(item, L),
      fisNum(r.seconds, 2), fisNum(r.time_points, 2, 'bold'), fisNum(r.air_total, 2, 'bold'),
      el('td', { class: 'num bold', title: r.turns_floor_applied ? 'ターン下限 0.3 を適用' : null, text: turnsText(r) }),
      ...fisScoreCells(item, L),
    ])];
  }
  const line1 = el('tr', { class: 'fis-l1' + sub }, [
    ...fisIdentityCells(item, L),
    fisNum(r.seconds, 2), fisNum(r.time_points, 2),
    ...fisAirCells(r.air, 0), el('td'),
    el('td', { class: 'bd', text: 'B:' }), ...fisJudgeCells(r.base, r.base_discard, L.nT), fisNum(r.base_total, 1),
    ...fisScoreCells(item, L),
  ]);
  const line2 = el('tr', { class: 'fis-l2' }, [
    ...fisBlanks(idCount(L) + 2),
    ...fisAirCells(r.air, 1), el('td'),
    el('td', { class: 'bd', text: 'D:' }), ...fisJudgeCells(r.ded, r.ded_discard, L.nT), fisNum(r.ded_total, 1),
    ...fisBlanks(scoreCount),
  ]);
  const line3 = el('tr', { class: 'fis-l3' }, [
    ...fisBlanks(idCount(L) + 1),
    fisNum(r.time_points, 2, 'bold rule'),
    ...fisBlanks(4), fisNum(r.air_total, 2, 'bold rule'),
    ...fisBlanks(L.nT + 1),
    el('td', { class: 'num bold rule', title: r.turns_floor_applied ? 'ターン下限 0.3 を適用' : null, text: turnsText(r) }),
    ...fisBlanks(scoreCount),
  ]);
  return [line1, line2, line3];
}

function renderFis(r, items) {
  const L = fisLayout(r, items);
  const th = (text, attrs = {}) => el('th', { class: 'no-sort ' + (attrs.class || ''), rowspan: attrs.rowspan, colspan: attrs.colspan, text });
  const ids = [th('Rank', { rowspan: 2 }), th('Bib', { rowspan: 2 })];
  if (L.intl) ids.push(th('FIS Code', { rowspan: 2 }));
  ids.push(th('Name', { rowspan: 2, class: 'name' }), th(L.intl ? 'NSA Code' : '所属', { rowspan: 2 }));
  if (L.intl) ids.push(th('YB', { rowspan: 2 }));
  else if (L.showClub) ids.push(th('クラブ', { rowspan: 2 }));
  if (L.qLayout) ids.push(th('', { rowspan: 2 }));
  const tail = [th('Run Score', { rowspan: 2 }), L.qLayout ? th('Best Score', { rowspan: 2 }) : null, th('Tie', { rowspan: 2 })];
  let head1, head2;
  if (L.detail) {
    head1 = el('tr', {}, [...ids, th('Time', { colspan: 2, class: 'grp' }), th('Air', { colspan: 5, class: 'grp' }),
      th('Turns', { colspan: L.nT + 2, class: 'grp' }), ...tail]);
    const judges = [];
    for (let i = 1; i <= L.nT; i++) judges.push(th('J' + i));
    head2 = el('tr', {}, [
      th('Seconds', { class: 'gl' }), th('Time Points', { class: 'gr' }),
      th('J' + L.airNos[0], { class: 'gl' }), th('J' + L.airNos[1]), th('Jump'), th('DD'), th('Total', { class: 'gr' }),
      th('B D', { class: 'gl' }), ...judges, th('Total', { class: 'gr' }),
    ]);
  } else {
    head1 = el('tr', {}, [...ids, th('Time', { colspan: 2, class: 'grp' }), th('Air', { rowspan: 2 }), th('Turns', { rowspan: 2 }), ...tail]);
    head2 = el('tr', {}, [th('Seconds', { class: 'gl' }), th('Time Points', { class: 'gr' })]);
  }
  const bodies = fisBlocks(items).map((block) => el('tbody', { class: 'fis-block' }, block.flatMap((item) => fisRows(item, L))));
  const legend = el('p', { class: 'meta', text: L.detail
    ? 'B: ベース点、D: 減点。' + (L.nT >= 5 ? '取り消し線の薄い数字は最高・最低のため除外された審判点（合計に入らない）。' : '3人制は除外なしで合計。')
      + '3行目の太字はタイム点・エア合計・ターン合計。ターンの下限は 0.3（* 印）。'
    : 'この段階は合計点まで照合済み（審判ごとの点は持っていません）。ターンの下限は 0.3（* 印）。' });
  return el('div', {}, [el('div', { class: 'table-wrap' }, el('table', { class: 'fis' }, [el('thead', {}, [head1, head2]), ...bodies])), legend]);
}

// ---- 狭い画面（スマホ）：1選手＝1カード（moguls-results と同じ形） --------------------------
// 見出し行（順位・名前・所属・Run Score）の下に、PDF と同じ並びのブロックを識別列抜きで置く。

const isDnf = (r) => !!(r.status && r.status !== 'OK');

function resMark(r) {
  return r.reserve_judge ? el('span', { class: 'fis-res', text: 'RES', title: 'リザーブジャッジが採点したラン（PDF の RES 印）' }) : null;
}

function scoreNode(r, value, cls) {
  if (value === null || value === undefined) {
    return isDnf(r) ? el('span', { class: cls + ' fis-status', text: r.status }) : el('span', { class: cls, text: '—' });
  }
  return el('span', { class: cls }, [num(value, 2), resMark(r)]);
}

// 上段：秒・タイム点（太字）｜エア2本（J J ジャンプ DD）｜エア計（太字）
// 下段：B: 審判点 計 ／ D: 審判点 計 ／ ターン計（太字）。得点までの段階は下段を合計だけにする
function miniBlock(r, L) {
  const jumpRow = (idx) => {
    const a = (r.air || [])[idx] || {};
    return el('tr', {}, [fisNum(a.J6, 1), fisNum(a.J7, 1), el('td', { class: 'jump', text: a.jump || '' }), fisNum(a.dd, 2)]);
  };
  const sec = num(r.seconds, 2);
  const timeAir = el('div', { class: 'fm-sec fm-timeair' }, [
    el('div', { class: 'fm-time' }, [
      el('span', { class: 'fm-sec-val', text: sec === null ? '' : sec + ' s' }),
      el('b', { text: num(r.time_points, 2) ?? '' }),
    ]),
    L.detail ? el('table', { class: 'fis fis-mini fm-air' }, el('tbody', {}, [jumpRow(0), jumpRow(1)])) : null,
    el('div', { class: 'fm-total' }, [el('span', { class: 'fm-lbl', text: 'エア' }), el('b', { text: num(r.air_total, 2) ?? '' })]),
  ]);
  const turnsRow = el('tr', { class: 'fis-l3' }, [
    el('td', { class: 'bd lbl rule', text: 'ターン' }),
    el('td', { class: 'num bold rule', colspan: L.detail ? L.nT + 1 : 1,
      title: r.turns_floor_applied ? 'ターン下限 0.3 を適用' : null, text: turnsText(r) }),
  ]);
  const rows = L.detail ? [
    el('tr', {}, [el('td', { class: 'bd', text: 'B:' }), ...fisJudgeCells(r.base, r.base_discard, L.nT), fisNum(r.base_total, 1, 'tot')]),
    el('tr', {}, [el('td', { class: 'bd', text: 'D:' }), ...fisJudgeCells(r.ded, r.ded_discard, L.nT), fisNum(r.ded_total, 1, 'tot')]),
    turnsRow,
  ] : [turnsRow];
  return el('div', { class: 'fis-mini-block' }, [timeAir, el('table', { class: 'fis fis-mini fm-turns' }, el('tbody', {}, rows))]);
}

function hasMarks(r) {
  return [r.seconds, r.time_points, r.air_total, r.base_total, r.ded_total, r.turns_total]
    .some((v) => v !== null && v !== undefined) || (r.air || []).length > 0;
}

function renderFisCards(r, items) {
  const L = fisLayout(r, items);
  const cards = fisBlocks(items).map((block) => {
    const head = block[0].run;
    const headScore = L.qLayout ? (head.best_score ?? head.run_score) : head.run_score;
    const org = L.intl ? head.noc : [head.affiliation, head.club].filter(Boolean).join(' ');
    const parts = [
      el('div', { class: 'fis-card-head' }, [
        el('span', { class: 'fis-rank', text: head.rank ?? '' }),
        el('span', { class: 'fis-name' }, [el('a', { href: athleteHref(head.athlete_id), text: head.name })]),
        el('span', { class: 'fis-noc', text: org || '' }),
        scoreNode(head, headScore, 'fis-score'),
      ]),
    ];
    for (const item of block) {
      const run = item.run;
      if (L.qLayout) {
        parts.push(el('div', { class: 'fis-sub-head' + (run.counting ? ' counting' : '') }, [
          el('span', { text: item.role === 'Q1ref' ? 'Q1' : (item.role || '') }),
          run.counting ? el('span', { class: 'badge official', text: '採用' }) : null,
          scoreNode(run, run.run_score, 'fis-sub-score'),
        ]));
      }
      if (!isDnf(run) || hasMarks(run)) parts.push(miniBlock(run, L));
    }
    return el('article', { class: 'rec fis-card' }, parts);
  });
  const legend = L.detail
    ? '上段：秒・タイム点（太字）｜エア2本の審判点・ジャンプ・DD｜エア計。下段：B: ベース点 ／ D: 減点 ／ ターン計。'
      + (L.nT >= 5 ? '取り消し線は最高・最低で除外された点。' : '')
    : '上段：秒・タイム点（太字）｜エア計。下段：ターン計。この段階は合計点まで照合済み。';
  // 見方の説明は折りたたむ（毎回読むものではないので、結果の1位を上に出す）
  return el('div', {}, [el('details', { class: 'meta small fis-legend' }, [el('summary', { text: 'カードの見方' }), legend]),
    el('div', { class: 'card-list' }, cards)]);
}

function nameCell(run) {
  return el('td', {}, [el('a', { href: athleteHref(run.athlete_id), text: run.name }), ...statusBadge(run)]);
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
    const head = el('div', { class: 'rec-head' }, [
      el('span', { class: 'rec-rank', text: run.rank ? run.rank + '位' : (run.status || '—') }),
      el('a', { class: 'rec-name', href: athleteHref(run.athlete_id), text: run.name }),
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
    const [events, lineList] = await Promise.all([data.events(), data.lines()]);
    event = events.find((e) => e.event_id === id);
    if (!event) throw new Error('大会 ' + id + ' がありません');
    lines = new Map(lineList.map((l) => [l.round_id, l]));
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
  // 表示形式（FIS形式／表形式）
  view = loadView();
  const viewButtons = [...document.querySelectorAll('#view-toggle button[data-view]')];
  const syncView = () => { for (const b of viewButtons) b.setAttribute('aria-pressed', String(b.dataset.view === view)); };
  for (const b of viewButtons) {
    b.addEventListener('click', () => {
      if (view === b.dataset.view) return;
      view = b.dataset.view;
      saveView(view);
      syncView();
      renderRound();
    });
  }
  syncView();
  const want = decodeURIComponent(location.hash.slice(1));
  current = event.rounds.find((r) => r.round_id === want) || event.rounds.find((r) => r.round === 'F2') || event.rounds[event.rounds.length - 1];
  renderChips();
  renderRound();
  onWidthChange(() => renderRound());
}

main();
