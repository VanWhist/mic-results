// ページ① リザルト。入口は検索。大会一覧から大会ページへ進む。
import * as data from './data.js';
import {
  el, clear, mountNav, errorBox, eventName, eventDates, seriesLabel, genderLabel, athleteMatches, athleteHref,
  eventHref, tierBadge, TIER_SHORT, gendersOf, meetButtonLabel,
} from './ui.js';

let events = [];
let athletes = [];
let manifest = null;

const PAGE_SIZE = 30;   // 一覧に最初に出すカードの数（「さらに表示」で足す。全部で約 390 枚あり、スマホでは非常に長かった）
const FILTER_KEYS = ['q', 'season', 'group', 'series', 'gender', 'discipline'];
const state = { q: '', season: '', group: '', series: '', gender: '', discipline: '', shown: PAGE_SIZE };

// 絞り込み・表示枚数・スクロール位置を、同じタブの中で覚える（大会を開いて戻ると初期状態に戻っていた）。
// sessionStorage が使えない環境では覚えないだけで、表示はできる
const STORE_KEY = 'mic-results.top';
function saveState() {
  try {
    const filters = document.getElementById('filters');
    sessionStorage.setItem(STORE_KEY, JSON.stringify({ ...state, scrollY: window.scrollY, filtersOpen: !!filters && !filters.hidden }));
  } catch (e) { /* 覚えられなくても表示は続ける */ }
}
function loadState() {
  try { return JSON.parse(sessionStorage.getItem(STORE_KEY) || 'null'); } catch (e) { return null; }
}

function eventMatches(ev, q) {
  const s = q.toLowerCase().replace(/\s+/g, '');
  return [ev.name_ja, ev.venue, ev.series_label, ev.season, ev.event_id].filter(Boolean)
    .some((t) => String(t).toLowerCase().replace(/\s+/g, '').includes(s));
}

function filteredEvents() {
  return events.filter((ev) => {
    if (state.season && ev.season !== state.season) return false;
    if (state.group && ev.series_group !== state.group) return false;
    if (state.series && ev.series !== state.series) return false;
    if (state.discipline && ev.discipline !== state.discipline) return false;
    if (state.gender && !(ev.rounds || []).some((r) => r.gender === state.gender)) return false;
    if (state.q && !eventMatches(ev, state.q)) return false;
    return true;
  });
}

function meetCard(ev) {
  const genders = [...new Set((ev.rounds || []).map((r) => r.gender))].map(genderLabel).join('・');
  const tiers = (ev.tiers || []).map((t) => TIER_SHORT[t] || t).join('・');
  return el('a', { class: 'meet-card', href: eventHref(ev.event_id) }, [
    el('div', { class: 'meet-date', text: eventDates(ev) }),
    el('div', { class: 'meet-name', text: ev.series_label || seriesLabel(ev.series) }),
    // 正式名は長いので一覧では 1 行に省略する（全文は大会ページ）
    el('div', { class: 'meet-sub meet-official', text: ev.name_ja || ev.venue || '', title: ev.name_ja || null }),
    // 大会名が無い（W杯など会場名で呼ぶ）大会は、会場を2度出さない
    el('div', { class: 'meet-sub', text: [ev.name_ja ? ev.venue : null, genders, tiers].filter(Boolean).join('　') }),
  ]);
}

// 同じ大会のモーグルとデュアルモーグル（events の meet）を 1 枚のカードにまとめ、種目ごとのボタンで大会ページへ進む
function meetGroupCard(members) {
  const head = members[0];
  const dates = members.flatMap((x) => [x.date_from, x.date_to]).filter(Boolean).sort();
  const genders = [...new Set(members.flatMap(gendersOf))].sort().map(genderLabel).join('・');
  const label = head.meet && head.meet.label ? '（' + head.meet.label + '）' : '';
  const span = !dates.length ? '—' : dates[0] === dates[dates.length - 1] ? dates[0] : dates[0] + ' 〜 ' + dates[dates.length - 1];
  return el('div', { class: 'meet-card meet-group' }, [
    el('div', { class: 'meet-date', text: span }),
    el('div', { class: 'meet-name', text: (head.series_label || seriesLabel(head.series)) + label }),
    el('div', { class: 'meet-sub meet-official', text: head.name_ja || head.venue || '', title: head.name_ja || null }),
    el('div', { class: 'meet-sub', text: [head.name_ja ? head.venue : null, genders].filter(Boolean).join('　') }),
    el('div', { class: 'meet-links' }, members.map((x) => el('a', { class: 'meet-link', href: eventHref(x.event_id) }, [
      el('span', { class: 'meet-link-name', text: meetButtonLabel(x, members) }),
      el('span', { class: 'meet-link-tier', text: (x.tiers || []).map((t) => TIER_SHORT[t] || t).join('・') }),
    ]))),
  ]);
}

// 一覧の並び（新しい順）のまま、同じ meet の大会は最初に出た位置に 1 枚にまとめる。絞り込みで 1 つしか残らなければ通常のカード
function listItems(list) {
  const byMeet = new Map();
  for (const ev of list) if (ev.meet) byMeet.set(ev.meet.id, [...(byMeet.get(ev.meet.id) || []), ev]);
  const out = [];
  const done = new Set();
  for (const ev of list) {
    const members = ev.meet ? byMeet.get(ev.meet.id) : null;
    if (!members || members.length < 2) { out.push(meetCard(ev)); continue; }
    if (done.has(ev.meet.id)) continue;
    done.add(ev.meet.id);
    const order = ev.meet.events;
    out.push(meetGroupCard([...members].sort((a, b) => order.indexOf(a.event_id) - order.indexOf(b.event_id))));
  }
  return out;
}

// ---- よく使う絞り込み（シーズン・区分）。詳しい絞り込みの select / checkbox と同じ state を使う ----
const GROUP_LABEL = { '国内': '国内', 'FIS系': 'FIS', 'W杯系': 'W杯・五輪' };

function quickChip(label, pressed, onclick) {
  return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(pressed), text: label, onclick });
}

function renderQuick() {
  const seasons = [...new Set(events.map((e) => e.season))].sort().reverse();
  const main = clear(document.getElementById('quick-main'));
  const seasonChoices = [[seasons[0], '今季 ' + seasons[0]], [seasons[1], '昨季'], ['', '全シーズン']].filter(([v]) => v !== undefined);
  for (const [v, label] of seasonChoices) main.append(quickChip(label, state.season === v, () => setFilter('season', v)));
  const groups = [...new Set(events.map((e) => e.series_group))].filter(Boolean);
  const order = ['国内', 'FIS系', 'W杯系'];
  groups.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  const grp = clear(document.getElementById('quick-group'));
  grp.append(quickChip('すべての大会', !state.group, () => setFilter('group', '')));
  for (const g of groups) grp.append(quickChip(GROUP_LABEL[g] || g, state.group === g, () => setFilter('group', g)));
}

function setFilter(key, value) {
  state[key] = value;
  state.shown = PAGE_SIZE;
  document.getElementById({ season: 'f-season', group: 'f-group' }[key]).value = value;
  renderQuick(); renderList(); updateReset();
}

function renderList() {
  const box = clear(document.getElementById('recent'));
  const moreBox = document.getElementById('recent-more');
  if (moreBox) clear(moreBox);
  const list = filteredEvents();
  document.getElementById('recent-title').textContent = FILTER_KEYS.some((k) => state[k])
    ? '大会（' + list.length + ' 件）' : '最近の大会';
  if (!list.length) {
    box.append(el('p', { class: 'meta', text: '該当する大会がありません。' }));
    saveState();
    return;
  }
  const items = listItems(list);
  box.append(...items.slice(0, state.shown));
  // 残りはカードの枚数（同じ大会のモーグル・デュアルモーグルは 1 枚にまとめるので、見出しの大会の件数より少ない）
  const rest = items.length - state.shown;
  if (rest > 0 && moreBox) {
    moreBox.append(el('button', { class: 'chip more-btn', type: 'button', text: 'さらに表示（あと ' + rest + ' 枚）',
      onclick: () => {
        const from = state.shown;
        state.shown += PAGE_SIZE;
        renderList();
        // 作り直した一覧の、足した最初のカードへフォーカスを移す（キーボード・読み上げで先頭からやり直しにならないように）
        const first = document.getElementById('recent').children[from];
        const link = first && (first.matches('a') ? first : first.querySelector('a'));
        if (link) link.focus({ preventScroll: true });
      } }));
  }
  saveState();
}

function renderSuggest(q) {
  const box = document.getElementById('suggest');
  clear(box);
  if (!q) { box.hidden = true; return; }
  const ath = athletes.filter((a) => athleteMatches(a, q)).slice(0, 8);
  const evs = events.filter((ev) => eventMatches(ev, q)).slice(0, 5);
  if (!ath.length && !evs.length) { box.hidden = true; return; }
  for (const a of ath) {
    box.append(el('a', { class: 'suggest-item', href: athleteHref(a.athlete_id) }, [
      el('span', { class: 'sug-name', text: a.name }),
      el('span', { class: 'sug-meta', text: [a.affiliation, a.club].filter(Boolean).join(' ') + '　' + (a.n_results || 0) + ' 本' }),
      el('span', { class: 'sug-go', text: '選手ページ →' }),
    ]));
  }
  for (const ev of evs) {
    box.append(el('a', { class: 'suggest-item', href: eventHref(ev.event_id) }, [
      el('span', { class: 'sug-name', text: eventName(ev) }),
      el('span', { class: 'sug-meta', text: eventDates(ev) }),
      el('span', { class: 'sug-go', text: '大会ページ →' }),
    ]));
  }
  box.hidden = false;
}

function fillSelect(id, values, labelOf, blank) {
  const sel = document.getElementById(id);
  clear(sel);
  sel.append(el('option', { value: '', text: blank }));
  for (const v of values) sel.append(el('option', { value: v, text: labelOf ? labelOf(v) : v }));
}

async function main() {
  mountNav('index.html');
  try {
    manifest = await data.manifest();
    [events, athletes] = await Promise.all([data.eventsIndex(), data.athletes()]);
  } catch (e) {
    document.getElementById('note-slot').append(errorBox(e.message));
    return;
  }
  fillSelect('f-season', [...new Set(events.map((e) => e.season))].sort().reverse(), null, 'すべて');
  fillSelect('f-group', [...new Set(events.map((e) => e.series_group))].sort(), null, 'すべて');
  fillSelect('f-series', [...new Set(events.map((e) => e.series))].sort(), seriesLabel, 'すべて');
  fillSelect('f-gender', ['M', 'W'], genderLabel, '男女');
  fillSelect('f-discipline', [...new Set(events.map((e) => e.discipline))].sort(), (d) => (d === 'DM' ? 'デュアル' : 'モーグル'), 'すべて');

  const q = document.getElementById('q');
  q.addEventListener('input', () => { state.q = q.value.trim(); state.shown = PAGE_SIZE; renderSuggest(state.q); renderList(); updateReset(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { q.value = ''; state.q = ''; state.shown = PAGE_SIZE; renderSuggest(''); renderList(); updateReset(); } });
  document.addEventListener('click', (e) => { if (!e.target.closest('.hero-search') && !e.target.closest('#suggest')) document.getElementById('suggest').hidden = true; });
  for (const [id, key] of [['f-season', 'season'], ['f-group', 'group'], ['f-series', 'series'], ['f-gender', 'gender'], ['f-discipline', 'discipline']]) {
    document.getElementById(id).addEventListener('change', (e) => { state[key] = e.target.value; state.shown = PAGE_SIZE; renderQuick(); renderList(); updateReset(); });
  }
  const filters = document.getElementById('filters');
  const toggle = document.getElementById('toggle-filters');
  toggle.addEventListener('click', () => {
    filters.hidden = !filters.hidden;
    toggle.setAttribute('aria-expanded', String(!filters.hidden));
    toggle.textContent = filters.hidden ? '詳しく絞り込む ▼' : '絞り込みを閉じる ▲';
  });
  document.getElementById('reset').addEventListener('click', () => {
    Object.assign(state, { q: '', season: '', group: '', series: '', gender: '', discipline: '', shown: PAGE_SIZE });
    q.value = '';
    for (const id of ['f-season', 'f-group', 'f-series', 'f-gender', 'f-discipline']) document.getElementById(id).value = '';
    renderSuggest(''); renderQuick(); renderList(); updateReset();
  });
  document.getElementById('note-slot').append(el('p', { class: 'meta', text:
    'データ版 ' + manifest.dataVersion + '　大会 ' + manifest.counts.events + ' 件 / ラウンド ' + manifest.counts.rounds + ' 件 / 記録 ' + manifest.counts.runs + ' 本 / 選手 ' + manifest.counts.athletes + ' 名' }));
  // 戻ってきたときだけ、前に見ていた絞り込み・検索語・表示枚数・位置を戻す（同じタブの中だけ）。
  // 「戻ってきた」＝ブラウザの「戻る／進む」か、大会ページの「← 大会一覧に戻る」（index.html#back）。
  // ヘッダーの「リザルト」やほかのページからのリンクでは最初の状態で開く（検索語が残って一覧が 0 件になるのを避ける）
  const nav = performance.getEntriesByType ? performance.getEntriesByType('navigation')[0] : null;
  const cameBack = (nav && nav.type === 'back_forward') || location.hash === '#back';
  if (location.hash === '#back') history.replaceState(null, '', location.pathname + location.search);
  const saved = cameBack ? loadState() : null;
  if (saved) {
    for (const [id, key] of [['f-season', 'season'], ['f-group', 'group'], ['f-series', 'series'], ['f-gender', 'gender'], ['f-discipline', 'discipline']]) {
      const sel = document.getElementById(id);
      sel.value = typeof saved[key] === 'string' ? saved[key] : '';
      if (sel.selectedIndex < 0) sel.value = '';   // 選べなくなった値（無くなったシーズンなど）は「すべて」に戻す
      state[key] = sel.value;
    }
    state.q = typeof saved.q === 'string' ? saved.q : '';
    q.value = state.q;
    state.shown = Math.max(PAGE_SIZE, Math.floor(Number(saved.shown)) || 0);
    if (saved.filtersOpen || state.series || state.gender || state.discipline) toggle.click();
  }
  renderQuick();
  renderList();
  updateReset();
  if (saved && saved.scrollY > 0) {
    window.scrollTo(0, saved.scrollY);
    saveState();   // renderList の中の保存は位置が 0 のままなので、戻した位置で保存し直す
  }
  // 位置は、ページを離れるときと、スクロールが止まったときに保存する（スマホではタブが破棄されて pagehide が出ないことがある）
  window.addEventListener('pagehide', saveState);
  let scrollTimer = null;
  window.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(saveState, 250); }, { passive: true });
}

function updateReset() {
  const active = state.q || state.season || state.group || state.series || state.gender || state.discipline;
  document.getElementById('reset').hidden = !active;
}

main();
