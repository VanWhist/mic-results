"""FIS 公式サイトからモーグル（単走）のリザルト PDF を探して取得する。

標準ライブラリのみ（urllib / re / json / html）。requests は使わない。

発見した URL の構造（2026-09-03 に実機で確認）
--------------------------------------------------
* カレンダー（大会一覧）
    https://www.fis-ski.com/DB/freestyle-freeski/moguls-aerials/calendar-results.html
        ?eventselection=results&sectorcode=FS&categorycode=WC&disciplinecode=MO&seasoncode=2025
  - パスは ``moguls-aerials`` を含める必要がある（``freestyle-freeski/calendar-results.html`` は
    200 を返すが大会行が空の殻）。
  - seasoncode はシーズン終了年（2024-25 シーズン = 2025）。
  - 1 大会 = ``<div class="table-row ..." id="<eventid>">``。会場名・国・日付・種目数（"2xMO 2xDM"）。

* 大会詳細（レース一覧 + 添付 PDF 一覧）
    https://www.fis-ski.com/DB/general/event-details.html?sectorcode=FS&eventid=<eventid>&seasoncode=<season>
  - 1 レース = ``<div class="table-row ...">`` で、``results.html?sectorcode=FS&raceid=<raceid>`` への
    リンク、codex（4 桁）、種目名（"Moguls" / "Moguls Qualification" / "Dual Moguls" / "Aerials"）、
    区分（WC / WSC / OWG / QUA）、性別（``gender__item_m`` = 男子、``gender__item_l`` = 女子）、
    ``data-date="YYYY-MM-DD"``。
  - **raceid は codex ではない**（例: codex 8105 の 2024-25 Ruka 男子は raceid=17202。
    raceid=8105 は 2015 年の別レース）。
  - 添付 PDF は ``<a href="https://www.fis-ski.com/DB/v2/download/competition-attachment/<uuid>.pdf"
    data-ga-codex="8105" data-ga-gender="M" data-ga-download="Results - Final Run 2">``。
    URL は UUID なので推測できない。この属性から codex・性別・種別を判定する。

* レース結果ページ
    https://www.fis-ski.com/DB/general/results.html?sectorcode=FS&raceid=<raceid>
  - 選手行 = ``<a class="table-row" href="...athlete-biography...">``。
    順位 / Bib / FIS コード / 氏名 / 生年 / NOC / 得点 / W杯ポイント / カップポイント。
  - 得点は「その選手が最後に滑ったラウンドのラン得点」1 つだけ（上位 6 名は決勝2、7〜16 位は決勝1、
    それ以下は予選）。ラウンド別・審判別の点は HTML には無い。
  - 添付 PDF は ``<div class="table-row pointer js-false-link" data-link="...pdf" data-ga-codex=...
    data-ga-download="...">``（大会詳細ページと同じ情報）。

* PDF ダウンロード
    https://www.fis-ski.com/DB/v2/download/competition-attachment/<uuid>.pdf
  - Content-Disposition に正式ファイル名（例 ``2025FS8105RLF2.pdf``）が入る。
  - 2024-25 Ruka 男子決勝2 をダウンロードしてローカル PDF と SHA-256 が一致することを確認済み。

添付 PDF の種別名（data-ga-download）と本ツールのラウンド記号・ローカル名の対応
    "Results - Qualification"        -> Q   (予選)
    "Results - Qualification Run 1"  -> Q1  (予選1)
    "Results - Qualification Run 2"  -> Q2  (予選2)
    "Results - Final Run 1"          -> F1  (決勝1)
    "Results - Final Run 2"          -> F2  (決勝2)
    "Results - Final"                -> 総合サマリー。審判別の点が無いので対象外
    "Start List - ..." / "Results Bracket - ..."（デュアル）/ "World Cup - ... Standing" -> 対象外

ローカルの PDF 命名（``全試合のリザルト`` と同じ）
    <季節フォルダ>/<会場>/<会場>_<男子|女子>モーグル<予選|予選1|予選2|決勝1|決勝2>_<codex>.pdf
    季節フォルダ: WC = "2024-25シーズン", WSC = "世界選手権", OWG = "オリンピック"

使い方
    python -m etl.fis_fetch --season 2025 --category WC --list
    python -m etl.fis_fetch --season 2025 --category WC --check-new --pdf-root <folder>
    python -m etl.fis_fetch --season 2025 --category WC --check-new --download --pdf-root <folder>
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://www.fis-ski.com"
CALENDAR_URL = (
    BASE_URL
    + "/DB/freestyle-freeski/moguls-aerials/calendar-results.html"
    + "?eventselection=results&sectorcode=FS&categorycode={category}&disciplinecode=MO&seasoncode={season}"
)
EVENT_URL = BASE_URL + "/DB/general/event-details.html?sectorcode=FS&eventid={eventid}&seasoncode={season}"
RACE_URL = BASE_URL + "/DB/general/results.html?sectorcode=FS&raceid={raceid}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 moguls-results-etl"
)
# FIS はアクセスが速すぎると 502 を返すことがあるので、リクエスト間に必ず間隔を空ける
REQUEST_DELAY_SEC = float(os.environ.get("FIS_FETCH_DELAY", "1.5"))
RETRIES = 3
TIMEOUT_SEC = 60

CATEGORIES = ("WC", "WSC", "OWG")

# data-ga-download -> ラウンド記号
RESULT_TITLE_TO_ROUND = {
    "Results - Qualification": "Q",
    "Results - Qualification Run 1": "Q1",
    "Results - Qualification Run 2": "Q2",
    "Results - Final Run 1": "F1",
    "Results - Final Run 2": "F2",
}
ROUND_ORDER = ("Q", "Q1", "Q2", "F1", "F2")
ROUND_JP = {"Q": "予選", "Q1": "予選1", "Q2": "予選2", "F1": "決勝1", "F2": "決勝2"}
GENDER_JP = {"M": "男子", "W": "女子"}

# FIS の会場名とローカルフォルダ名が違う場合の対応表（必要に応じて追記）
VENUE_FOLDER_ALIASES = {
    "Waterville Valley Resort": "Waterville",
}


class FisFetchError(RuntimeError):
    """FIS サイトへのアクセス失敗、または HTML 構造が想定と違うとき。"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_last_request_at = 0.0
_page_cache: dict[str, str] = {}


def _throttle() -> None:
    global _last_request_at
    wait = REQUEST_DELAY_SEC - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _open(url: str) -> tuple[bytes, dict[str, str]]:
    """URL を GET してバイト列とヘッダを返す。5xx はリトライする。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"})
    last_err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
                return resp.read(), {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
        time.sleep(REQUEST_DELAY_SEC * attempt * 2)
    raise FisFetchError(f"GET failed: {url} ({last_err})")


def fetch_text(url: str) -> str:
    """HTML を取得（同一 URL は同一プロセス内でキャッシュ）。"""
    if url not in _page_cache:
        body, _ = _open(url)
        _page_cache[url] = body.decode("utf-8", "replace")
    return _page_cache[url]


# ---------------------------------------------------------------------------
# HTML ユーティリティ
# ---------------------------------------------------------------------------
def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _table_rows(page: str) -> list[str]:
    """``<div class="table-row ...">`` で始まるブロックに分割する（次の table-row 直前まで）。"""
    starts = [m.start() for m in re.finditer(r'<div class="table-row[^"]*"', page)]
    rows = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(page)
        rows.append(page[s:e])
    return rows


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r'\b' + re.escape(name) + r'="([^"]*)"', tag)
    return html_mod.unescape(m.group(1)) if m else None


def season_label(category: str, season_code: int) -> str:
    """seasoncode 2025 -> "2024-25"。"""
    return f"{season_code - 1}-{str(season_code)[-2:]}"


def season_folder(category: str, season_code: int) -> str:
    """ローカルの季節フォルダ名。WC はシーズン、WSC/OWG は固定フォルダ。"""
    if category == "WSC":
        return "世界選手権"
    if category == "OWG":
        return "オリンピック"
    return f"{season_label(category, season_code)}シーズン"


def venue_folder(venue: str) -> str:
    v = VENUE_FOLDER_ALIASES.get(venue, venue)
    return re.sub(r'[\\/:*?"<>|]', "", v).strip()


def local_pdf_name(venue: str, gender: str, round_code: str, codex: str) -> str:
    return f"{venue_folder(venue)}_{GENDER_JP[gender]}モーグル{ROUND_JP[round_code]}_{codex}.pdf"


# ---------------------------------------------------------------------------
# 1. カレンダー -> 大会 -> レース
# ---------------------------------------------------------------------------
def list_calendar_events(season_code: int, category: str) -> list[dict]:
    """カレンダーページの大会行を返す（eventid, venue, nation, date_text, disciplines, event_url）。"""
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}: {category}")
    url = CALENDAR_URL.format(category=category, season=season_code)
    page = fetch_text(url)
    events = []
    for row in _table_rows(page):
        m = re.match(r'<div class="table-row[^"]*"[^>]*\bid="(\d+)"', row)
        if not m:
            continue
        eventid = m.group(1)
        venue = re.search(r'font_lg_large">\s*(.*?)\s*</div>', row, re.S)
        nation = re.search(r'country__name-short">\s*(\w+)\s*<', row)
        date_m = re.search(r'class="pl-1 pl-sm-0[^"]*"\s+href="[^"]*event-details[^"]*"[^>]*>\s*(.*?)\s*</a>', row, re.S)
        clips = re.findall(r'<div class="clip">\s*(.*?)\s*</div>', row, re.S)
        events.append(
            {
                "eventid": eventid,
                "venue": _strip_tags(venue.group(1)) if venue else "",
                "nation": nation.group(1) if nation else "",
                "date_text": _strip_tags(date_m.group(1)) if date_m else "",
                "disciplines": _strip_tags(clips[1]) if len(clips) > 1 else "",
                "event_url": EVENT_URL.format(eventid=eventid, season=season_code),
            }
        )
    if not events and "modal_calendar_download" not in page:
        # 大会 0 件のシーズン（例: 世界選手権の無い年）でもカレンダー本体は描画される。
        # それすら無ければ、ブロックされたか URL パスが変わった可能性が高い。
        raise FisFetchError(f"calendar page has no calendar body (blocked or layout changed): {url}")
    return events


def list_event_races(event_url: str) -> list[dict]:
    """大会詳細ページのレース行を全部返す（種目を問わない）。"""
    page = fetch_text(event_url)
    races = []
    for row in _table_rows(page):
        rid = re.search(r"results\.html\?sectorcode=FS&(?:amp;)?raceid=(\d+)", row)
        codex = re.search(r'raceid=\d+"[^>]*>\s*(\d{4})\s*</a>', row)
        if not rid or not codex:
            continue  # 添付ファイルの行など
        disc = re.search(r'<div class="clip">\s*(.*?)\s*</div>', row, re.S)
        cat = re.search(r'justify-center hidden-sm-down"[^>]*>\s*([A-Z]{2,4})\s*</a>', row)
        gen = re.search(r"gender__item gender__item_(m|l|a)", row)
        date = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', row)
        tm = re.search(r'data-time="(\d{2}:\d{2})"', row)
        races.append(
            {
                "raceid": rid.group(1),
                "codex": codex.group(1),
                "discipline_text": _strip_tags(disc.group(1)) if disc else "",
                "race_category": cat.group(1) if cat else "",
                "gender": {"m": "M", "l": "W", "a": "X"}.get(gen.group(1), "?") if gen else "?",
                "date": date.group(1) if date else "",
                "time": tm.group(1) if tm else "",
                "race_url": RACE_URL.format(raceid=rid.group(1)),
            }
        )
    return races


def is_single_moguls(discipline_text: str) -> bool:
    t = discipline_text.lower()
    return t.startswith("moguls") and "dual" not in t


def list_events(season_code: int, category: str) -> list[dict]:
    """モーグル単走のレース（大会 × 性別 × codex）を列挙する。

    戻り値の各要素:
      codex, season(=seasoncode), season_label, series(=category), venue, nation, date, gender('M'|'W'),
      discipline('MO'), race_kind('Moguls'|'Moguls Qualification'), race_category('WC'|'QUA'|...),
      raceid, race_url, eventid, event_url
    「Moguls Qualification」は予選日が別 codex になっている大会（例 2024-25 Bakuriani 男子 9022、
    世界選手権・オリンピック）で現れる。デュアルモーグル・エアリアルは含めない。
    """
    out = []
    for ev in list_calendar_events(season_code, category):
        for r in list_event_races(ev["event_url"]):
            if not is_single_moguls(r["discipline_text"]):
                continue
            out.append(
                {
                    "codex": r["codex"],
                    "season": season_code,
                    "season_label": season_label(category, season_code),
                    "series": category,
                    "venue": ev["venue"],
                    "nation": ev["nation"],
                    "date": r["date"],
                    "gender": r["gender"],
                    "discipline": "MO",
                    "race_kind": r["discipline_text"],
                    "race_category": r["race_category"],
                    "raceid": r["raceid"],
                    "race_url": r["race_url"],
                    "eventid": ev["eventid"],
                    "event_url": ev["event_url"],
                }
            )
    out.sort(key=lambda x: (x["date"], x["codex"]))
    return out


# ---------------------------------------------------------------------------
# 2. リザルト PDF の列挙
# ---------------------------------------------------------------------------
def list_attachments(page_url: str) -> list[dict]:
    """レース結果ページまたは大会詳細ページの添付ファイルを全部返す。

    各要素: url, title(data-ga-download), codex(data-ga-codex), gender(data-ga-gender)
    """
    page = fetch_text(page_url)
    seen = set()
    atts = []
    # 大会詳細: <a href="...pdf" data-ga-...>、レース結果: <div data-link="...pdf" data-ga-...>
    for m in re.finditer(r"<(?:a|div)\b[^>]*\bdata-ga-download=\"[^\"]*\"[^>]*>", page):
        tag = m.group(0)
        url = _attr(tag, "href") or _attr(tag, "data-link")
        if not url or "competition-attachment" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        atts.append(
            {
                "url": url,
                "title": _attr(tag, "data-ga-download") or "",
                "codex": _attr(tag, "data-ga-codex") or "",
                "gender": _attr(tag, "data-ga-gender") or "",
            }
        )
    return atts


def list_result_pdfs(race_url: str, codex: str | None = None) -> list[dict]:
    """モーグル単走のリザルト PDF（審判別の点が載る "Results - <ラウンド>"）を返す。

    race_url にはレース結果ページでも大会詳細ページでも渡せる（添付一覧は同じ）。
    codex を渡すとその codex の分だけに絞る。
    戻り値: [{round: 'Q'|'Q1'|'Q2'|'F1'|'F2', title, url, codex, gender}]（ROUND_ORDER 順）
    除外: "Results - Final"（総合サマリー）、スタートリスト、デュアルのブラケット、スタンディング。
    同じ codex に Q1/Q2 がある場合、"Results - Qualification" は Q1+Q2 のまとめとみなして除外する
    （2023 世界選手権 Bakuriani の形式）。
    """
    atts = list_attachments(race_url)
    if codex is not None:
        atts = [a for a in atts if a["codex"] == str(codex)]
    per_codex: dict[str, list[dict]] = {}
    for a in atts:
        rnd = RESULT_TITLE_TO_ROUND.get(a["title"])
        if rnd is None:
            continue
        per_codex.setdefault(a["codex"], []).append({"round": rnd, **a})
    out = []
    for cx, items in per_codex.items():
        rounds = {i["round"] for i in items}
        if "Q" in rounds and ({"Q1", "Q2"} & rounds):
            items = [i for i in items if i["round"] != "Q"]
        out.extend(items)
    out.sort(key=lambda x: (x["codex"], ROUND_ORDER.index(x["round"])))
    return out


# ---------------------------------------------------------------------------
# 3. HTML 結果表（第5層の外部照合用）
# ---------------------------------------------------------------------------
def fetch_html_results(race_url: str) -> list[dict]:
    """FIS のレース結果ページ（HTML）の選手行を返す。

    HTML に載っている得点は **1 選手につき 1 つ** で、その選手が最後に滑ったラウンドのラン得点
    （決勝2 進出者は決勝2、決勝1 まで進んだ選手は決勝1、予選落ちは予選の点）。ラウンド別の点や
    審判別の点、タイム・エア・ターンの内訳は HTML には無い。したがって第5層では
    「最終順位」と「最後のラウンドのラン得点」だけを PDF 側と突き合わせる。
    順位が無い行（DNF/DNS/DSQ 等）は rank=None、run_score=None。

    戻り値: [{rank, bib, fis_code, name, yb, noc, run_score, wc_points, cup_points}]
    """
    page = fetch_text(race_url)
    rows = []
    for m in re.finditer(r'<a class="table-row"\s+href="([^"]*athlete-biography[^"]*)"[^>]*>(.*?)</a>', page, re.S):
        blk = m.group(2)
        rank = re.search(r'justify-right pr-1 bold">\s*(\d*)\s*<', blk)
        bib = re.search(r'hidden-sm-down gray">\s*(\d*)\s*<', blk)
        fis = re.search(r'justify-right gray">\s*(\d+)\s*<', blk)
        name = re.search(r'justify-left bold">\s*(.*?)\s*</div>', blk, re.S)
        yb = re.search(r'hidden-sm-down justify-left">\s*(\d{4})\s*<', blk)
        noc = re.search(r'country__name-short">\s*(\w+)\s*<', blk)
        score = re.search(r'blue bold">\s*([\d.]+)\s*<', blk)
        pts = re.findall(r'justify-right (?:"|hidden-xs")>\s*([\d.]+)\s*<', blk)
        rows.append(
            {
                "rank": int(rank.group(1)) if rank and rank.group(1) else None,
                "bib": int(bib.group(1)) if bib and bib.group(1) else None,
                "fis_code": fis.group(1) if fis else None,
                "name": _strip_tags(name.group(1)) if name else "",
                "yb": int(yb.group(1)) if yb else None,
                "noc": noc.group(1) if noc else "",
                "run_score": float(score.group(1)) if score else None,
                "wc_points": float(pts[0]) if len(pts) > 0 else None,
                "cup_points": float(pts[1]) if len(pts) > 1 else None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 4. ダウンロード
# ---------------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_pdf(url: str, dest_path: str | Path) -> str:
    """PDF を dest_path に保存し SHA-256（16進）を返す。PDF でなければ保存せず例外。"""
    dest = Path(dest_path)
    body, headers = _open(url)
    if not body.startswith(b"%PDF"):
        raise FisFetchError(f"not a PDF ({headers.get('content-type')}): {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    os.replace(tmp, dest)
    return hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# 5. ローカルとの突き合わせ
# ---------------------------------------------------------------------------
def find_local_pdf(pdf_root: Path, folder: str, gender: str, round_code: str, codex: str) -> Path | None:
    """<pdf_root>/<季節フォルダ>/ 以下から「*_<男子|女子>モーグル<ラウンド>_<codex>.pdf」を探す。会場名は問わない。"""
    base = pdf_root / folder
    if not base.is_dir():
        return None
    suffix = f"_{GENDER_JP[gender]}モーグル{ROUND_JP[round_code]}_{codex}.pdf"
    alt = f"_{GENDER_JP[gender]}{ROUND_JP[round_code]}.pdf"  # 北京2022 の手動命名: 北京オリンピック_男子予選1.pdf
    for p in base.rglob("*.pdf"):
        if p.name.endswith(suffix):
            return p
    for p in base.rglob("*.pdf"):
        if p.name.endswith(alt) and 'モーグル' not in p.name:
            return p
    return None


def check_new(season_code: int, category: str, pdf_root: Path, download: bool, log=print) -> dict:
    """FIS 上の単走リザルト PDF をローカルと比べ、無いものを報告（--download なら取得）する。"""
    events = list_events(season_code, category)
    folder = season_folder(category, season_code)
    report = {
        "season": season_code,
        "season_label": season_label(category, season_code),
        "category": category,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season_folder": folder,
        "races": len(events),
        "expected": [],
        "missing": [],
        "downloaded": [],
        "errors": [],
    }
    for ev in events:
        # 添付一覧は大会詳細ページに全部あるので、レースページを追加で取りに行かない
        try:
            pdfs = list_result_pdfs(ev["event_url"], codex=ev["codex"])
        except FisFetchError as e:
            report["errors"].append(str(e))
            log(f"  ! {e}")
            continue
        for p in pdfs:
            gender = p["gender"] if p["gender"] in GENDER_JP else ev["gender"]
            if gender not in GENDER_JP:
                continue
            name = local_pdf_name(ev["venue"], gender, p["round"], ev["codex"])
            rec = {
                "codex": ev["codex"],
                "venue": ev["venue"],
                "date": ev["date"],
                "gender": gender,
                "round": p["round"],
                "title": p["title"],
                "url": p["url"],
                "local_name": name,
            }
            report["expected"].append(rec)
            found = find_local_pdf(pdf_root, folder, gender, p["round"], ev["codex"])
            if found:
                continue
            dest = pdf_root / folder / venue_folder(ev["venue"]) / name
            rec = {**rec, "dest": str(dest)}
            report["missing"].append(rec)
            log(f"  MISSING {folder}/{venue_folder(ev['venue'])}/{name}  <- {p['url']}")
            if download:
                try:
                    digest = download_pdf(p["url"], dest)
                    report["downloaded"].append({**rec, "sha256": digest})
                    log(f"    downloaded sha256={digest}")
                except FisFetchError as e:
                    report["errors"].append(str(e))
                    log(f"    ! {e}")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FIS モーグル単走リザルト PDF の探索・取得")
    ap.add_argument("--season", type=int, required=True, help="seasoncode（2024-25 シーズンなら 2025）")
    ap.add_argument("--category", default="WC", help="WC / WSC / OWG。カンマ区切りで複数可")
    ap.add_argument("--list", action="store_true", help="単走レースと PDF を一覧表示")
    ap.add_argument("--check-new", action="store_true", help="ローカルに無い PDF を報告")
    ap.add_argument("--download", action="store_true", help="--check-new で無かった PDF を取得")
    ap.add_argument("--pdf-root", help="ローカル PDF のルート（例: source_pdfs）")
    ap.add_argument("--report", help="結果を JSON で書き出すパス")
    ap.add_argument("--html-results", metavar="RACE_URL", help="HTML 結果表を JSON で表示（第5層の確認用）")
    args = ap.parse_args(argv)

    categories = [c.strip().upper() for c in args.category.split(",") if c.strip()]
    for c in categories:
        if c not in CATEGORIES:
            ap.error(f"unknown category: {c}")

    if args.html_results:
        print(json.dumps(fetch_html_results(args.html_results), ensure_ascii=False, indent=1))
        return 0

    if args.list:
        for cat in categories:
            events = list_events(args.season, cat)
            print(f"== {cat} season {args.season} ({season_label(cat, args.season)}): {len(events)} single-moguls races")
            for ev in events:
                print(f"{ev['date']} {ev['venue']} ({ev['nation']}) {ev['gender']} codex={ev['codex']} "
                      f"[{ev['race_kind']}/{ev['race_category']}] raceid={ev['raceid']}")
                for p in list_result_pdfs(ev["event_url"], codex=ev["codex"]):
                    print(f"    {p['round']:<3} {p['title']:<32} {p['url']}")
        return 0

    if args.check_new:
        if not args.pdf_root:
            ap.error("--check-new には --pdf-root が必要")
        pdf_root = Path(args.pdf_root)
        reports = []
        for cat in categories:
            print(f"== check {cat} season {args.season} against {pdf_root}")
            rep = check_new(args.season, cat, pdf_root, args.download)
            print(f"   races={rep['races']} expected_pdfs={len(rep['expected'])} missing={len(rep['missing'])} "
                  f"downloaded={len(rep['downloaded'])} errors={len(rep['errors'])}")
            reports.append(rep)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
        return 2 if any(r["errors"] for r in reports) else 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
