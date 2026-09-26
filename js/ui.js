// 画面まわりの小さな共通部品。フレームワークは使わない。
// moguls-results の js/ui.js を土台に、系列・段階（tier）・MIC 表示を足している。

import { SITE_TITLE, REPORT_EMAIL, REPORT_ISSUES_URL } from './config.js';

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

// ---- 数値 -----------------------------------------------------------------
// 取れなかった値は「0」ではなく空欄にする。推測で埋めない。
export function num(v, digits = 2) {
  if (v === null || v === undefined || v === '') return null;
  return Number(v).toFixed(digits);
}

export function cell(value, digits = 2, extraClass = '') {
  const text = num(value, digits);
  return el('td', {
    class: ('num ' + (text === null ? 'blank ' : '') + extraClass).trim(),
    text: text === null ? '—' : text,
  });
}

export function cents(x) {
  if (x === null || x === undefined || x === '') return null;
  return Math.round(Number(x) * 100);
}

export function fromCents(c, digits = 2) {
  return (c / 100).toFixed(digits);
}

export function diffSpan(c, digits = 2) {
  if (c === null || c === undefined) return el('span', { class: 'diff-zero', text: '—' });
  const cls = c > 0 ? 'diff-pos' : c < 0 ? 'diff-neg' : 'diff-zero';
  const sign = c > 0 ? '+' : c < 0 ? '−' : '±';
  return el('span', { class: cls, text: sign + fromCents(Math.abs(c), digits) });
}

// ---- ラベル -----------------------------------------------------------------
export const SERIES_LABEL = {
  WC: 'ワールドカップ', WSC: '世界選手権', OWG: 'オリンピック',
  EC: 'ヨーロッパカップ', NAC: 'ノルアムカップ', ANC: 'ANC（豪・NZ）', AC: 'アジアカップ',
  WJC: 'ジュニア世界選手権', YOG: 'ユースオリンピック', FIS: 'FISレース', NC: 'FIS NC', OPN: 'FISオープン',
  SAJ_AJ: '全日本選手権', SAJ_AJJR: '全日本ジュニア', SAJ_KOKUSPO: '国スポ', SAJ_JOC: 'JOCジュニアオリンピックカップ',
  SAJ_A: 'A級公認大会', SAJ_B: 'B級公認大会', SAJ_SEL: '選考会',
};
export const ROUND_LABEL = { Q: '予選', Q1: '予選1', Q2: '予選2', F1: '決勝1', F2: '決勝2', F3: '決勝3' };
export const TIER_LABEL = { detail: 'ジャッジ点まで照合済み', score: '得点まで照合済み', rank: '順位のみ' };
export const TIER_SHORT = { detail: '明細あり', score: '得点のみ', rank: '順位のみ' };
export const LAYER_LABEL = {
  layer0: '第0層 完全性（登録済みの大会・ラウンド・人数と一致、PDF の SHA-256 一致）',
  layer1: '第1層 二重読み取り（文字行の読み取りと座標の読み取りの全項目一致）',
  layer2: '第2層 再計算（タイム点・エア・ターン・合計を大会の規則から再計算）',
  layer3: '第3層 再構成（順位・同点・通過者の検算）',
  layer4: '第4層 横断整合（選手 ID↔氏名・所属・審判・ペースタイム）',
  layer5: '第5層 外部照合（公式サイトの順位表と突き合わせ）',
  golden: '正解データ回帰（PDF を目視して作った正解との一致）',
};
export const LAYER_STATUS = {
  ok: '照合済み', skipped: '未実施', error: '不一致', upstream_missing: '公式側に照合先なし', 'n/a': 'この段階では対象外',
};

export function layerMark(s) {
  if (s === 'ok') return { mark: '✓ ', cls: 'verify-ok' };
  if (s === 'skipped' || s === 'n/a') return { mark: '－ ', cls: 'meta' };
  if (s === 'upstream_missing') return { mark: '△ ', cls: 'verify-gap' };
  return { mark: '✗ ', cls: 'verify-ng' };
}
export function layerStatusLabel(s) { return LAYER_STATUS[s] || s || '—'; }

export function seriesLabel(s) { return SERIES_LABEL[s] || s || '—'; }
export function genderLabel(g) {
  if (g === 'M') return '男子';
  if (g === 'W' || g === 'F' || g === 'L') return '女子';
  return g || '—';
}
// ラウンド名は印字の見出し語（round_text）を優先する。全日本の「決勝」は W杯の「決勝1」に当たるが、
// 保護者が見る画面では大会の言葉で出す。
export function roundLabel(r) {
  if (r && typeof r === 'object') return r.round_text || ROUND_LABEL[r.round] || r.round || '—';
  return ROUND_LABEL[r] || r || '—';
}

export function tierBadge(tier) {
  return el('span', { class: 'badge tier tier-' + tier, text: TIER_LABEL[tier] || tier, title: tierHelp(tier) });
}
export function tierHelp(tier) {
  if (tier === 'detail') return '審判ごとの点まで読み取り、規則から再計算して印字と一致することを確かめた記録です。';
  if (tier === 'score') return '順位・得点・タイムだけを読み取った記録です。審判ごとの点はありません。';
  if (tier === 'rank') return '順位だけの記録です（デュアルモーグルなど）。';
  return '';
}

export function eventName(ev) {
  return ev.season + ' ' + (ev.series_label || seriesLabel(ev.series)) + (ev.name_ja ? '　' + ev.name_ja : (ev.venue ? '　' + ev.venue : ''));
}
export function eventShortName(ev) {
  return (ev.series_label || seriesLabel(ev.series)) + ' ' + (ev.season || '');
}

export function eventDates(ev) {
  let from = ev.date_from, to = ev.date_to;
  if (!from || !to) {
    const ds = (ev.rounds || []).map((r) => r.date).filter(Boolean).sort();
    if (!ds.length) return '—';
    from = ds[0];
    to = ds[ds.length - 1];
  }
  return from === to ? from : from + ' 〜 ' + to;
}

export function verificationState(v) {
  if (!v) return 'ng';
  const vals = Object.values(v);
  if (vals.some((s) => !['ok', 'skipped', 'upstream_missing', 'n/a'].includes(s))) return 'ng';
  return vals.includes('upstream_missing') ? 'gap' : 'ok';
}
export function verificationBadge(round) {
  const st = verificationState(round.verification);
  if (st === 'ng') return { text: '⚠ 検証に未確認の層があります', cls: 'verify-ng' };
  return { text: '✓ ' + (TIER_LABEL[round.tier] || '照合済み'), cls: 'verify-ok' };
}

export function statusBadge(run) {
  const out = [];
  if (run.status && run.status !== 'OK') out.push(el('span', { class: 'badge status', text: run.status }));
  if (run.reserve_judge) out.push(el('span', { class: 'badge res', text: 'リザーブジャッジ採点' }));
  return out;
}

// ---- 誤り報告リンク ----------------------------------------------------------
export function reportLink(ctx) {
  const subject = '[mic-results] 誤り報告 ' + (ctx.round_id || ctx.run_id || '');
  const body = [
    'round_id: ' + (ctx.round_id || '—'),
    ctx.run_id ? 'run_id: ' + ctx.run_id : null,
    'dataVersion: ' + (ctx.dataVersion || '—'),
    ctx.pdf ? 'pdf: ' + ctx.pdf : null,
    'ページ: ' + location.href,
    '',
    '誤りの内容（どの選手・どの列・正しい値）:',
    '',
  ].filter((x) => x !== null).join('\n');
  let href;
  if (REPORT_EMAIL) {
    href = 'mailto:' + REPORT_EMAIL + '?subject=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
  } else {
    href = REPORT_ISSUES_URL + '?title=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body);
  }
  return el('a', { href, target: REPORT_EMAIL ? null : '_blank', rel: 'noopener', text: '誤りを報告' });
}

// ---- ナビ -------------------------------------------------------------------
export function navbar(active) {
  const links = [
    ['index.html', 'リザルト'],
    ['athlete.html', '選手'],
    ['about.html', 'データについて'],
  ];
  return el('header', { class: 'site' }, [
    el('h1', { text: SITE_TITLE }),
    el('nav', {}, links.map(([href, label]) =>
      el('a', { href, class: href === active ? 'active' : '', text: label }))),
  ]);
}
export function mountNav(active) {
  const header = navbar(active);
  document.body.prepend(header);
  // 固定ヘッダの高さを CSS 変数に入れる（大会ページのラウンド切替をその直下に固定するため）
  const sync = () => document.documentElement.style.setProperty('--nav-h', header.offsetHeight + 'px');
  sync();
  window.addEventListener('resize', sync);
}

export function errorBox(message) {
  return el('div', { class: 'notice', text: 'データを読めませんでした：' + message });
}

export function makeSortable(table, rows, render, initial) {
  let key = initial ? initial.key : null;
  let dir = initial ? initial.dir : 1;
  const heads = [...table.tHead.rows[table.tHead.rows.length - 1].cells];
  function apply() {
    if (key) {
      const sorted = [...rows].sort((a, b) => {
        const x = a[key], y = b[key];
        const xn = x === null || x === undefined || x === '';
        const yn = y === null || y === undefined || y === '';
        if (xn && yn) return 0;
        if (xn) return 1;
        if (yn) return -1;
        if (typeof x === 'number' && typeof y === 'number') return (x - y) * dir;
        return String(x).localeCompare(String(y), 'ja') * dir;
      });
      render(sorted);
    } else {
      render(rows);
    }
    for (const h of heads) {
      const hk = h.dataset.key;
      if (!hk) continue;
      if (hk === key) h.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
      else h.removeAttribute('aria-sort');
    }
  }
  for (const h of heads) {
    if (!h.dataset.key) { h.classList.add('no-sort'); continue; }
    h.addEventListener('click', () => {
      if (key === h.dataset.key) dir = -dir;
      else { key = h.dataset.key; dir = h.dataset.numeric ? -1 : 1; }
      apply();
    });
  }
  apply();
  return { update(next) { rows = next; apply(); } };
}

export const PER_PAGE = 50;
export function paginate(list, page, perPage = PER_PAGE) {
  const pages = Math.max(1, Math.ceil(list.length / perPage));
  const current = Math.min(Math.max(1, page), pages);
  const start = (current - 1) * perPage;
  return { items: list.slice(start, start + perPage), page: current, pages, from: list.length ? start + 1 : 0,
    to: Math.min(start + perPage, list.length), total: list.length };
}
export function renderPager(node, view, onGo) {
  clear(node);
  const n = (v) => v.toLocaleString('ja-JP');
  node.append(el('span', { class: 'pager-count' }, [
    el('strong', { text: n(view.total) + '件' }),
    document.createTextNode(view.total ? '　' + n(view.from) + '–' + n(view.to) + '件を表示' : '　該当なし'),
  ]));
  if (view.pages <= 1) return;
  node.append(el('span', { class: 'pager-nav' }, [
    el('button', { class: 'chip', text: '‹ 前へ', disabled: view.page <= 1, onclick: () => onGo(view.page - 1) }),
    el('span', { class: 'pager-pos', text: view.page + ' / ' + view.pages }),
    el('button', { class: 'chip', text: '次へ ›', disabled: view.page >= view.pages, onclick: () => onGo(view.page + 1) }),
  ]));
}

const NARROW = window.matchMedia('(max-width: 700px)');
export function isNarrow() { return NARROW.matches; }
export function onWidthChange(fn) {
  const handler = () => fn(NARROW.matches);
  if (NARROW.addEventListener) NARROW.addEventListener('change', handler);
  else NARROW.addListener(handler);
}

export function noteLayers(headline, detailNodes, aboutAnchor) {
  const body = el('div', { class: 'note-body', hidden: true }, detailNodes);
  const toggle = el('button', {
    class: 'link-button', text: '詳しく見る',
    onclick: () => { body.hidden = !body.hidden; toggle.textContent = body.hidden ? '詳しく見る' : '閉じる'; },
  });
  return el('div', { class: 'note' }, [
    el('div', { class: 'note-head' }, [
      el('span', { class: 'note-icon', text: 'ⓘ' }),
      el('span', { text: headline }),
      toggle,
      aboutAnchor === false ? null : el('a', { class: 'note-more', href: 'about.html', text: 'データについて' }),
    ]),
    body,
  ]);
}

// ---- 選手検索 ---------------------------------------------------------------
// 氏名（漢字）と別名（かな・ローマ字）、FIS コード、SAJ 番号に部分一致させる。空白の有無は無視する。
export function athleteMatches(a, q) {
  const s = q.toLowerCase().replace(/\s+/g, '');
  if ((a.name || '').toLowerCase().replace(/\s+/g, '').includes(s)) return true;
  if ((a.fis_code || '') === q || (a.saj_no || '') === q) return true;
  return (a.aliases || []).some((n) => String(n).toLowerCase().replace(/\s+/g, '').includes(s));
}
export function athleteHref(id) { return 'athlete.html?id=' + encodeURIComponent(id); }
export function eventHref(id, roundId) {
  return 'event.html?id=' + encodeURIComponent(id) + (roundId ? '#' + encodeURIComponent(roundId) : '');
}
export function queryParam(name) { return new URLSearchParams(location.search).get(name); }
