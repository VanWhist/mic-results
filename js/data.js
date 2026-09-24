// データの読み込み。
// manifest.json を最初に読み、そこに書かれたハッシュ付きファイル名を読む。
// 各ファイルには manifest の dataVersion をクエリに付ける。付けないと、更新したのに
// ブラウザが古い JSON を使い続けて「反映されない」事故が起きる。
//
// このアプリは表示するだけで、正しさの判断（列ずれ・名寄せ・切り捨て・欠測処理）は
// すべて ETL 側（Python）で済ませてある。ここに条件分岐を増やさないこと。

// js/ の1つ上（リポジトリのルート）を基準にする。GitHub Pages のサブパスでも動く。
const BASE = new URL('../', import.meta.url).href;
const cache = new Map();

let manifestPromise = null;

export function manifest() {
  if (!manifestPromise) {
    manifestPromise = fetch(BASE + 'data/manifest.json', { cache: 'no-cache' })
      .then((r) => {
        if (!r.ok) throw new Error('manifest.json が読めません (' + r.status + ')');
        return r.json();
      });
  }
  return manifestPromise;
}

async function load(path) {
  if (cache.has(path)) return cache.get(path);
  const m = await manifest();
  const p = fetch(BASE + 'data/' + path + '?v=' + encodeURIComponent(m.dataVersion))
    .then((r) => {
      if (!r.ok) throw new Error(path + ' が読めません (' + r.status + ')');
      return r.json();
    });
  cache.set(path, p);
  return p;
}

async function fileOf(key) {
  const m = await manifest();
  const f = m.files && m.files[key];
  if (!f) throw new Error('manifest.json に ' + key + ' がありません');
  return f;
}

export const events = async () => load(await fileOf('events'));
export const athletes = async () => load(await fileOf('athletes'));
export const lines = async () => load(await fileOf('lines'));
export const rules = async () => load(await fileOf('rules'));

export async function seasons() {
  const m = await manifest();
  return Object.keys((m.files && m.files.runs) || {}).sort();
}

export async function runs(season) {
  const m = await manifest();
  const f = m.files && m.files.runs && m.files.runs[season];
  if (!f) throw new Error('シーズン ' + season + ' のランファイルがありません');
  return load(f);
}

// 全シーズンのランを1つの配列にする。件数は数千本なので一括で持てる。
export async function allRuns() {
  const list = await Promise.all((await seasons()).map((s) => runs(s)));
  return list.flat();
}

// round_id → { event, round } の索引。ラン側からラウンド情報（ペースタイム・審判）を引く。
export async function roundIndex() {
  const map = new Map();
  for (const ev of await events()) {
    for (const r of ev.rounds || []) map.set(r.round_id, { event: ev, round: r });
  }
  return map;
}

export async function athleteMap() {
  const list = await athletes();
  return new Map(list.map((a) => [a.athlete_id, a]));
}

export async function lineMap() {
  const list = await lines();
  return new Map(list.map((l) => [l.round_id, l]));
}
