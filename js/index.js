// ページ① リザルト。入口は検索。大会一覧から大会ページへ進む。
import * as data from './data.js';
import {
  el, clear, mountNav, errorBox, eventName, eventDates, seriesLabel, genderLabel, athleteMatches, athleteHref,
  eventHref, tierBadge, TIER_SHORT, micBadge,
} from './ui.js';

let events = [];
let athletes = [];
let manifest = null;
let micEvents = new Set();   // MIC 選手が出た大会

const state = { q: '', season: '', group: '', series: '', gender: '', discipline: '', mic: false };

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
    if (state.mic && !micEvents.has(ev.event_id)) return false;
    if (state.q && !eventMatches(ev, state.q)) return false;
    return true;
  });
}

function meetCard(ev) {
  const genders = [...new Set((ev.rounds || []).map((r) => r.gender))].map(genderLabel).join('・');
  const tiers = (ev.tiers || []).map((t) => TIER_SHORT[t] || t).join('・');
  return el('a', { class: 'meet-card', href: eventHref(ev.event_id) }, [
    el('div', { class: 'meet-date', text: eventDates(ev) }),
    el('div', { class: 'meet-name', text: (ev.series_label || seriesLabel(ev.series)) + (micEvents.has(ev.event_id) ? '　★MIC出場' : '') }),
    // 正式名は長いので一覧では 1 行に省略する（全文は大会ページ）
    el('div', { class: 'meet-sub meet-official', text: ev.name_ja || ev.venue || '', title: ev.name_ja || null }),
    // 大会名が無い（W杯など会場名で呼ぶ）大会は、会場を2度出さない
    el('div', { class: 'meet-sub', text: [ev.name_ja ? ev.venue : null, genders, tiers].filter(Boolean).join('　') }),
  ]);
}

// ---- よく使う絞り込み（MIC のみ・シーズン・区分）。詳しい絞り込みの select / checkbox と同じ state を使う ----
const GROUP_LABEL = { '国内': '国内', 'FIS系': 'FIS', 'W杯系': 'W杯・五輪' };

function quickChip(label, pressed, onclick) {
  return el('button', { class: 'chip', type: 'button', 'aria-pressed': String(pressed), text: label, onclick });
}

function renderQuick() {
  const seasons = [...new Set(events.map((e) => e.season))].sort().reverse();
  const main = clear(document.getElementById('quick-main'));
  if (micEvents.size) main.append(quickChip('★ MIC のみ', state.mic, () => setFilter('mic', !state.mic)));
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
  if (key === 'mic') document.getElementById('f-mic').checked = value;
  else document.getElementById({ season: 'f-season', group: 'f-group' }[key]).value = value;
  renderQuick(); renderList(); updateReset();
}

function renderList() {
  const box = clear(document.getElementById('recent'));
  const list = filteredEvents();
  document.getElementById('recent-title').textContent = state.q || state.season || state.group || state.series || state.gender || state.mic
    ? '大会（' + list.length + ' 件）' : '最近の大会';
  if (!list.length) {
    box.append(el('p', { class: 'meta', text: '該当する大会がありません。' }));
    return;
  }
  for (const ev of list) box.append(meetCard(ev));
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
      el('span', { class: 'sug-name' }, [a.name, ' ', micBadge(a)]),
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
    [events, athletes] = await Promise.all([data.events(), data.athletes()]);
    const micIds = new Set(athletes.filter((a) => a.mic).map((a) => a.athlete_id));
    if (micIds.size) {
      const runs = await data.allRuns();
      for (const r of runs) if (micIds.has(r.athlete_id)) micEvents.add(r.event_id);
    }
  } catch (e) {
    document.getElementById('note-slot').append(errorBox(e.message));
    return;
  }
  fillSelect('f-season', [...new Set(events.map((e) => e.season))].sort().reverse(), null, 'すべて');
  fillSelect('f-group', [...new Set(events.map((e) => e.series_group))].sort(), null, 'すべて');
  fillSelect('f-series', [...new Set(events.map((e) => e.series))].sort(), seriesLabel, 'すべて');
  fillSelect('f-gender', ['M', 'W'], genderLabel, '男女');
  fillSelect('f-discipline', [...new Set(events.map((e) => e.discipline))].sort(), (d) => (d === 'DM' ? 'デュアル' : 'モーグル'), 'すべて');
  document.getElementById('f-mic').disabled = micEvents.size === 0;

  const q = document.getElementById('q');
  q.addEventListener('input', () => { state.q = q.value.trim(); renderSuggest(state.q); renderList(); updateReset(); });
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape') { q.value = ''; state.q = ''; renderSuggest(''); renderList(); } });
  document.addEventListener('click', (e) => { if (!e.target.closest('.hero-search') && !e.target.closest('#suggest')) document.getElementById('suggest').hidden = true; });
  for (const [id, key] of [['f-season', 'season'], ['f-group', 'group'], ['f-series', 'series'], ['f-gender', 'gender'], ['f-discipline', 'discipline']]) {
    document.getElementById(id).addEventListener('change', (e) => { state[key] = e.target.value; renderQuick(); renderList(); updateReset(); });
  }
  document.getElementById('f-mic').addEventListener('change', (e) => { state.mic = e.target.checked; renderQuick(); renderList(); updateReset(); });
  const filters = document.getElementById('filters');
  const toggle = document.getElementById('toggle-filters');
  toggle.addEventListener('click', () => {
    filters.hidden = !filters.hidden;
    toggle.setAttribute('aria-expanded', String(!filters.hidden));
    toggle.textContent = filters.hidden ? '詳しく絞り込む ▼' : '絞り込みを閉じる ▲';
  });
  document.getElementById('reset').addEventListener('click', () => {
    Object.assign(state, { q: '', season: '', group: '', series: '', gender: '', discipline: '', mic: false });
    q.value = '';
    for (const id of ['f-season', 'f-group', 'f-series', 'f-gender', 'f-discipline']) document.getElementById(id).value = '';
    document.getElementById('f-mic').checked = false;
    renderSuggest(''); renderQuick(); renderList(); updateReset();
  });
  document.getElementById('note-slot').append(el('p', { class: 'meta', text:
    'データ版 ' + manifest.dataVersion + '　大会 ' + manifest.counts.events + ' 件 / ラウンド ' + manifest.counts.rounds + ' 件 / 記録 ' + manifest.counts.runs + ' 本 / 選手 ' + manifest.counts.athletes + ' 名' }));
  renderQuick();
  renderList();
}

function updateReset() {
  const active = state.q || state.season || state.group || state.series || state.gender || state.discipline || state.mic;
  document.getElementById('reset').hidden = !active;
}

main();
