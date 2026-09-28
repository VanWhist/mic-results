"""Parser B: text-line based parser for FIS moguls single-run result PDFs.

Independent verification parser. It must NOT share code with parser_a.
Only ``pdfplumber.Page.extract_text()`` lines + regular expressions are used
(no word coordinates).

Public API::

    meta, records = parse_moguls_results(path)
"""

import re

import pdfplumber

PARSER_VERSION = "B-1.7"

STATUS_WORDS = ("DNF", "DNS", "DSQ")
WEEKDAY = r"(?:MON|TUE|WED|THU|FRI|SAT|SUN)"

# ---------------------------------------------------------------------------
# Row line regexes
# ---------------------------------------------------------------------------
# athlete line 1: [rank] bib fiscode <rest>
RE_L1 = re.compile(r"^(?:(?P<rank>\d{1,3}) )?(?P<bib>\d{1,3}) (?P<code>\d{7}) (?P<rest>.+)$")
# name / NOC / YB inside <rest>.  NOC may be glued to the last name token.
RE_NAME_NOC_YB = re.compile(r"^(?P<name>.+?) ?(?P<noc>[A-Z]{3}) (?P<yb>\d{4})(?: (?P<tail>.*))?$")
RE_NAME_NOC_NOYB = re.compile(
    r"^(?P<name>.+?) ?(?P<noc>[A-Z]{3}) (?P<tail>(?:Q[12] )?(?:DNF|DNS|DSQ|\d+\.\d\d).*)$"
)
# 国名コードが途中で切れて印字される行（Calgary 2018「DESMARAIS-GILBERTC AN 1997」）
RE_NAME_NOC_SPLIT = re.compile(r"^(?P<name>.+?)(?:(?P<a1>[A-Z]) (?P<a2>[A-Z]{2})|(?P<b1>[A-Z]{2}) (?P<b2>[A-Z])) (?P<yb>\d{4})(?: (?P<tail>.*))?$")
RE_QLABEL = re.compile(r"^Q(?P<q>[12])(?: (?P<tail>.*))?$")
# second (or later) score block of a Q-layout athlete
RE_QBLOCK = re.compile(r"^Q(?P<q>[12]) (?P<tail>.+)$")
# scored tail: seconds timepoints J6 J7 jump DD B: J1..J5 BaseTotal RunScore [extras]
RE_SCORED = re.compile(
    r"^(?:(?P<runlbl>[A-Z]{1,2}\d?): )?(?P<sec>\d+\.\d\d) (?P<tp>\d+\.\d\d) (?P<j6>\d+\.\d) (?P<j7>\d+\.\d) "
    r"(?:(?P<jump>[0-9A-Za-z]+) )?(?P<dd>-?\d\.\d\d\d?) B: (?P<rest>.+)$"
)
# D line: [wrapped name tokens] J6 J7 jump DD D: deductions
RE_DLINE = re.compile(
    r"^(?:(?P<wrap>[^\d\s][^\d]*?) )?(?:(?P<runlbl>[A-Z]{1,2}\d?): )?(?P<j6>\d+\.\d) (?P<j7>\d+\.\d) "
    r"(?:(?P<jump>[0-9A-Za-z]+) )?(?P<dd>-?\d\.\d\d\d?) D:(?P<rest>.*)$"
)
RE_DED = re.compile(r"-\d+\.\d")
RE_SECTION_LINE = re.compile(r"^(Super Final|Final ?\d?|Qualification ?\d?)$", re.I)  # 総合の報告の区切り
RE_RUN_NOCOLON = re.compile(r"^[QF]\d$")  # ANC 2018 の総合: 各行の走行の印（コロンなし）
# 総合の報告で同じ選手の続きの走り（決勝 2 の選手の決勝 1・予選など）の 1 行目。行頭に国名と生年（ANC 2018）や
# 走行の印（F1: / Q1 / 無し）が付く
RE_CONT = re.compile(r"^(?:[A-Z]{3} \d{4} )?(?:(?P<lbl>[QF]\d|SF|PH):? )?(?P<tail>\d+\.\d\d \d+\.\d\d .* B: .+|DNF|DNS|DSQ)$")
RE_RUNLBL = re.compile(r"^[A-Z]{1,2}\d?:$")  # 行頭の走行ラベル（Q1:・F1:・PH:）
# 3rd line: time points, air total, turns total
RE_L3 = re.compile(r"^(?:(?P<wrap>[^\d\s][^\d]*?) )?(?P<tp>\d+\.\d\d) (?P<air>\d+\.\d\d) (?P<turns>\d+\.\d\d?)$")
# 名前の折り返しとみなす語（人名に使う文字だけ）。見出し・フッタの語（Forerunners・Moguls Women・www.… など）は除く
RE_NAME_TOKEN = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-\.]*$")
NOT_NAME = {"Forerunners", "Forerunner", "Moguls", "Women", "Men", "Ladies", "Run", "FIS", "Results", "RESULTS",
            "Conditions", "Course", "Weather", "NOTE", "Legend", "Jury", "Qualification", "Final", "QUALIFICATION", "FINAL",
            "Race", "Rac", "Points", "Poi", "nts", "e"}  # 縦書きの表頭の切れ端（NAC 2018 Calgary の「Rac nts」）
RE_NUM = re.compile(r"^\d+\.\d+$")
RE_DIVIDER = re.compile(r"^(Qualified to |Not Qualified|Qualified$)")
RE_DIGIT = re.compile(r"\d")
# 表頭の「B D J1 J2 J3 Total」からターン審判の人数を数える（W杯は 5 人、世界ジュニア 2022 などは 3 人）
RE_TURNS_HDR = re.compile(r"\bB D((?: J\d)+) Total\b")
_n_turns = 5  # parse_moguls_results が PDF ごとに設定する
_best_col = False  # 表頭に Best Score 列がある（NAC 2022 の「eS B co e r s e t」、総合の報告の「Best」「ScoreScore」）
_tie_col = True  # 表頭に Tie 列がある（総合の報告は Tie の代わりに Race Points で、右端の数値は同点の値ではない）

# ---------------------------------------------------------------------------
# Header / footer / jury regexes
# ---------------------------------------------------------------------------
RE_FOOTER_DATE = re.compile(
    r"^(?:" + WEEKDAY + r" )?(?P<date>\d{1,2} [A-Z]{3} \d{4}) / (?P<body>.+?) / (?P<codex>\d+)(?: Report [Cc]reated .*)?$"
)
RE_FOOTER_OTHER = re.compile(r"^(Report [Cc]reated|www\.fis-ski\.com|Timing/Scoring|Page \d+/\d+|FRS[A-Z]+-|Data processing and timing|FIS Results provided by|#FISfreestyle|C\d+M Page|Data Processing & Timing)")
RE_JURY_START = re.compile(r"^Jury (?:Technical Data|Course Details|Course Data)")
RE_JURY_END = re.compile(r"^(Forerunners:|Conditions on [Cc]ourse:?$|Conditions on course:|Legend:|Progression|Note:|NOTE$|Turnsscore|Timepoints)")

RE_STD_DATE = re.compile(r"^(?:.*?\([A-Z]{3}\) )?(?P<date>" + WEEKDAY + r" \d{1,2} [A-Z]{3} \d{4}) Start Time: (?P<time>\S+)")
RE_OLY_DATE = re.compile(r"^(?P<date>" + WEEKDAY + r" \d{1,2} [A-Z]{3} \d{4}) (?P<round>.+)$")
RE_OLY_START = re.compile(r"^Start Time (?P<time>\d{1,2}:\d{2})")
RE_RESULTS_ROUND = re.compile(r"^Results (?P<round>.+)$")
RE_AFTER_Q = re.compile(r"^After (?P<round>Qualification \d)$")
RE_NUM_COMP = re.compile(r"^Number of Competitors: (?P<n>\d+)")
RE_EVENT = re.compile(r"((?:Men's|Women's|Ladies') Moguls)")
RE_VENUE_HDR = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý0-9 .'/\-]+ \([A-Z]{3}\)$")
RE_HEADER = re.compile(
    r"^(FIS .*(?:WORLD CUP|World Cup|Championships|CHAMPIONSHIPS)|(?:\d{4} )?FIS FREESTYLE|Results\b|MO$|Number of Competitors|Time Air Turns|Rank Bib|Points$|"
    r"After Qualification|Start Time |\S+/\S+$|.*Freestyle Skiing$|.*(?:Men's|Women's|Ladies') Moguls$|RESULTS |[A-Z0-9 ]*RESULTS$)"
)

# technical data suffixes (appear at the end of jury lines)
RE_TECH = [
    ("course_name", re.compile(r" ?Course Name:? (?P<v>.+)$")),
    ("course_length_m", re.compile(r" ?Length:? (?P<v>[\d.]+)m$")),
    ("course_gate_width", re.compile(r" ?Course / Gate Width:? (?P<v>[\d.]+m / [\d.]+m)$")),
    ("_course_width", re.compile(r" ?Course Width:? (?P<v>[\d.]+)m$")),
    ("gradient_deg", re.compile(r" ?Gradient:? (?P<v>[\d.]+)°$")),
    ("pace_time", re.compile(r" ?Pace Time:? (?P<v>[\d.]+)s?$")),
    ("homologation", re.compile(r" ?Homologation Number:? (?P<v>.+)$")),
    ("_judges_hdr", re.compile(r" ?Judges$")),
]
RE_JUDGE = re.compile(r"(?:^| )Judge (?P<no>\d) \((?P<role>[^)]+)\):? (?P<rest>.+)$")
RE_UPPER_TOKEN = re.compile(r"[A-ZÀ-Ý][A-ZÀ-Ý'\-]+")
RE_NOC = re.compile(r"[A-Z]{3}")


def _f(s):
    return float(s) if s is not None else None


def _is_header_line(l):
    if RE_HEADER.match(l):
        return True
    if RE_STD_DATE.match(l) or RE_OLY_DATE.match(l) or RE_VENUE_HDR.match(l):
        return True
    # bilingual Olympic header lines (CJK characters)
    if any(ord(ch) > 0x2E80 for ch in l):
        return True
    return False


# ---------------------------------------------------------------------------
# Record helpers
# ---------------------------------------------------------------------------
def _new_record(athlete, page, q_block, counting):
    return {
        "rank": athlete["rank"],
        "bib": athlete["bib"],
        "fis_code": athlete["fis_code"],
        "name": athlete["name"],
        "noc": athlete["noc"],
        "yb": athlete["yb"],
        "tie": None,
        "reserve_judge": False,
        "page": page,
        "status": "OK",
        "seconds": None,
        "time_points": None,
        "air_jumps": [],
        "air_total": None,
        "base_scores": [],
        "base_total": None,
        "ded_scores": [],
        "ded_total": None,
        "turns_total": None,
        "run_score": None,
        "q_block": q_block,
        "best_score": None,
        "counting": counting,
    }


def _parse_block_tail(rec, tail, warnings, page, line):
    """Fill *rec* from the text after YB (and after the Q label if any).

    Returns True when the block is a scored block (D line + 3rd line follow),
    False for a status block (DNF/DNS/DSQ).
    """
    tail = tail.strip()
    tokens = tail.split()
    if tokens and RE_RUNLBL.match(tokens[0]):
        tokens = tokens[1:]
        tail = " ".join(tokens)
    if tokens and tokens[0] in STATUS_WORDS:
        rec["status"] = tokens[0]
        if not _tie_col:
            # 右端の数値: 2 つなら Best Score と FIS ポイント（ANC 2019「DNF 60.90 99」）、1 つなら FIS ポイントだけ
            # （ANC 2018「Q1 DNF 0.00」「F1 DNF 75.00」）
            nums = [x for x in tokens[1:] if RE_NUM.match(x) or x.isdigit()]
            if _best_col and len(nums) >= 2:
                rec["best_score"] = _f(nums[0])
            return False
        _apply_extras(rec, tokens[1:], warnings, page, line)
        return False
    m = RE_SCORED.match(tail)
    if not m:
        warnings.append((page, "unrecognised score tail", line))
        return True
    rec["seconds"] = _f(m.group("sec"))
    rec["time_points"] = _f(m.group("tp"))
    if m.group("jump") is not None:  # DD -1.000 は差し引くジャンプ。DD 0 の「NJ 0.000」はジャンプなしの印字（0 点の欄として持つ）
        rec["air_jumps"].append({"J6": _f(m.group("j6")), "J7": _f(m.group("j7")),
                                 "jump": m.group("jump"), "DD": _f(m.group("dd"))})
    rest = m.group("rest").split()
    n = _n_turns
    if len(rest) == n + 1 or (len(rest) == n + 2 and rest[n + 1] in ("Q", "RES")):
        # ターンの合計（Total）欄が空欄の様式: 審判 n 人の点のあとは Run Score
        rec["base_scores"] = [_f(x) for x in rest[:n]]
        rec["run_score"] = _f(rest[n])
        _apply_extras(rec, rest[n + 1:], warnings, page, line)
        return True
    if len(rest) < n + 2:
        warnings.append((page, "short B: line", line))
        return True
    rec["base_scores"] = [_f(x) for x in rest[:n]]
    rec["base_total"] = _f(rest[n])
    rec["run_score"] = _f(rest[n + 1])
    _apply_extras(rec, rest[n + 2:], warnings, page, line)
    return True


def _apply_extras(rec, extras, warnings, page, line):
    """Handle the optional trailing tokens: best score (Q-layout), Tie, RES."""
    nums = []
    for t in extras:
        if t == "RES":
            rec["reserve_judge"] = True
        elif t == "Q":
            rec["qualified_mark"] = True  # 次のラウンドへの通過の印（2017-18 の様式）
        elif RE_NUM.match(t):
            nums.append(float(t))
        else:
            if not (t.endswith("…") and not _tie_col):  # 途中で切れた Race Points（ANC 2024「29…」）
                warnings.append((page, "unexpected trailing token %r" % t, line))
    if (rec["q_block"] is not None and rec["counting"] or _best_col) and nums:
        rec["best_score"] = nums.pop(0)
    if nums and not _tie_col:
        nums = []  # Race Points（FIS ポイント）。記録には持たない
    if nums:
        rec["tie"] = nums.pop(0)
    if nums:
        warnings.append((page, "extra numeric tokens %r" % nums, line))


def _parse_dline(rec, m, warnings, page, line):
    if m.group("jump") is not None:
        rec["air_jumps"].append({"J6": _f(m.group("j6")), "J7": _f(m.group("j7")),
                                 "jump": m.group("jump"), "DD": _f(m.group("dd"))})
    elif any(_f(m.group(k)) > 0 for k in ("j6", "j7")):
        warnings.append((page, "air line without jump code but non-zero scores", line))
    deds = [float(x) for x in RE_DED.findall(m.group("rest"))]
    n = _n_turns
    if len(deds) == n + 1:
        rec["ded_scores"] = deds[:n]
        rec["ded_total"] = deds[n]
    elif len(deds) == n:
        rec["ded_scores"] = deds
        # the total column is blank when every deduction is -0.0
        rec["ded_total"] = 0.0
        if any(d != 0.0 for d in deds):
            warnings.append((page, "deductions all printed but total blank and not all zero", line))
    else:
        warnings.append((page, "unexpected deduction count %d" % len(deds), line))
        rec["ded_scores"] = deds


def _parse_l3(rec, m, warnings, page, line):
    tp = _f(m.group("tp"))
    if rec["time_points"] is not None and abs(rec["time_points"] - tp) > 0.005:
        warnings.append((page, "time points mismatch line1=%s line3=%s" % (rec["time_points"], tp), line))
    if rec["time_points"] is None:
        rec["time_points"] = tp
    rec["air_total"] = _f(m.group("air"))
    rec["turns_total"] = _f(m.group("turns"))


def _append_name(athlete, athlete_recs, fragment):
    athlete["name"] = (athlete["name"] + " " + fragment).strip()
    for r in athlete_recs:
        r["name"] = athlete["name"]


# ---------------------------------------------------------------------------
# Meta helpers
# ---------------------------------------------------------------------------
def _parse_header_line(l, meta):
    m = RE_EVENT.search(l)
    if m and not meta.get("event"):
        meta["event"] = m.group(1)
    m = RE_STD_DATE.match(l)
    if m:
        meta["date"] = m.group("date")
        meta["start_time"] = m.group("time")
        return
    m = RE_OLY_DATE.match(l)
    if m and not meta.get("date"):
        meta["date"] = m.group("date")
        meta["round_raw"] = m.group("round").strip()
        return
    m = RE_OLY_START.match(l)
    if m and not meta.get("start_time"):
        meta["start_time"] = m.group("time")
        return
    m = RE_RESULTS_ROUND.match(l)
    if m and not meta.get("round_raw"):
        meta["round_raw"] = m.group("round").strip()
        return
    m = RE_AFTER_Q.match(l)
    if m:
        meta["round_after"] = m.group("round")
        return
    m = RE_NUM_COMP.match(l)
    if m and meta.get("num_competitors") is None:
        meta["num_competitors"] = int(m.group("n"))
        return
    if RE_VENUE_HDR.match(l) and not meta.get("venue_header"):
        meta["venue_header"] = l.strip()


def _parse_footer_line(l, meta):
    m = RE_FOOTER_DATE.match(l)
    if not m:
        return False
    if not meta.get("codex"):
        meta["codex"] = m.group("codex")
        meta["venue"] = m.group("body").strip()
        meta["footer_date"] = m.group("date")
    return True


def _split_official(text, meta):
    """'Chief of Competition FITZGERALD Joseph T. CAN' -> officials entry.

    The role is the run of leading tokens up to the first all-uppercase token
    (the surname); 'FIS' is allowed inside the role.
    """
    tokens = text.split()
    role = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if RE_UPPER_TOKEN.fullmatch(t) and t != "FIS":
            break
        role.append(t)
        i += 1
    name_tokens = tokens[i:]
    if not role or not name_tokens:
        if text != "Officials":
            meta["unparsed_lines"].append(("jury", text))
        return
    noc = None
    if len(name_tokens) >= 2 and RE_NOC.fullmatch(name_tokens[-1]):
        noc = name_tokens[-1]
        name_tokens = name_tokens[:-1]
    meta["officials"].append({"role": " ".join(role), "name": " ".join(name_tokens), "noc": noc})


def _parse_jury_line(l, meta):
    l = l.strip()
    if not l:
        return
    # 1) technical-data suffix
    for key, rx in RE_TECH:
        m = rx.search(l)
        if not m:
            continue
        if not key.startswith("_"):
            v = m.group("v").strip()
            if key == "course_gate_width":
                cw, gw = v.split(" / ")
                meta["course_width_m"] = float(cw.rstrip("m"))
                meta["gate_width_m"] = float(gw.rstrip("m"))
            elif key in ("course_length_m", "gradient_deg", "pace_time"):
                meta[key] = float(v)
            else:
                meta[key] = v
        l = l[: m.start()].strip()
        break
    if not l:
        return
    # 2) judge part
    m = RE_JUDGE.search(l)
    if m:
        rest = m.group("rest").split()
        noc = None
        if len(rest) >= 2 and RE_NOC.fullmatch(rest[-1]):
            noc = rest[-1]
            rest = rest[:-1]
        meta["judges"].append({"judge_no": int(m.group("no")), "role": m.group("role"),
                               "name": " ".join(rest), "noc": noc})
        l = l[: m.start()].strip()
    if not l:
        return
    # 3) official
    _split_official(l, meta)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_moguls_results(path):
    meta = {
        "event": None, "round": None, "date": None, "start_time": None,
        "venue": None, "codex": None, "num_competitors": None,
        "pace_time": None, "course_length_m": None, "course_width_m": None,
        "gate_width_m": None, "gradient_deg": None,
        "judges": [], "officials": [],
        "unparsed_lines": [], "warnings": [],
        "parser_version": PARSER_VERSION, "source_file": path,
    }
    records = []
    warnings = meta["warnings"]

    # row state
    athlete = None          # identity dict of the current athlete
    athlete_recs = []       # records belonging to the current athlete
    cur = None              # record currently being filled (expects D / L3)
    expect = "L1"           # 'L1' | 'D' | 'L3'
    last_kind = None        # 'status' | 'scored' | 'D' | 'L3'
    in_jury = False

    def close_block(page):
        nonlocal cur, expect
        if cur is not None and expect != "L1":
            warnings.append((page, "block incomplete (expected %s)" % expect, cur["name"]))
        cur = None
        expect = "L1"

    global _n_turns, _best_col, _tie_col
    section = None
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ""
        # Q1/Q2 の 2 ブロックの表は Q2 のブロックがある。無ければ行頭の Q1/F1 は走行の印として読み飛ばす
        q2_doc = any(re.search(r"(?m) Q2 (?:\d+\.\d\d|DN[SF]|DSQ)", pg.extract_text() or "") for pg in pdf.pages)
        mh = RE_TURNS_HDR.search(first)
        _n_turns = len(mh.group(1).split()) if mh else 5
        hdr_line = next((l for l in first.split("\n") if l.startswith("Rank Bib")), "")
        _best_col = bool(re.search(r"\bB co e r s e t\b", first)) or "Best" in first.split("Rank Bib")[0][-200:] \
            or "ScoreScore" in hdr_line or "Score Score" in hdr_line
        _tie_col = " Tie" in hdr_line
        meta["n_turns_judges"] = _n_turns
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            lines = [l.rstrip() for l in text.split("\n") if l.strip()]
            skip_rest = False      # after Forerunners/Conditions/Legend until footer
            in_table = False       # このページで表頭（Rank Bib）より下に来たか

            for l in lines:
                if _parse_footer_line(l, meta) or RE_FOOTER_OTHER.match(l):
                    skip_rest = False
                    in_jury = False
                    continue
                if l.startswith("Rank Bib"):
                    in_table = True
                if RE_SECTION_LINE.match(l.strip()) and in_table:
                    close_block(pno)
                    section = re.sub(r"\s+", " ", l.strip()).title()
                    athlete = None
                    continue
                if _is_header_line(l):
                    _parse_header_line(l, meta)
                    if in_jury and (l.startswith("Number of Competitors") or l.startswith("Rank Bib")):
                        in_jury = False
                    continue
                if RE_JURY_START.match(l):
                    close_block(pno)
                    in_jury = True
                    continue
                if RE_JURY_END.match(l):
                    in_jury = False
                    skip_rest = True
                    continue
                if skip_rest:
                    continue
                if in_jury:
                    _parse_jury_line(l, meta)
                    continue
                if RE_DIVIDER.match(l):
                    continue

                # --- athlete line 1 -------------------------------------
                m = RE_L1.match(l)
                if m:
                    close_block(pno)
                    rest = m.group("rest")
                    m2 = RE_NAME_NOC_YB.match(rest)
                    yb = None
                    noc = None
                    if m2:
                        yb = int(m2.group("yb"))
                        noc = m2.group("noc")
                    else:
                        m2 = RE_NAME_NOC_NOYB.match(rest)
                        if m2:
                            noc = m2.group("noc")
                        else:
                            m2 = RE_NAME_NOC_SPLIT.match(rest)
                            if m2:
                                yb = int(m2.group("yb"))
                                noc = (m2.group("a1") or m2.group("b1")) + (m2.group("a2") or m2.group("b2"))
                    if not m2:
                        meta["unparsed_lines"].append((pno, l))
                        continue
                    athlete = {
                        "rank": int(m.group("rank")) if m.group("rank") else None,
                        "bib": int(m.group("bib")),
                        "fis_code": m.group("code"),
                        "name": m2.group("name").strip(),
                        "noc": noc,
                        "yb": yb,
                    }
                    athlete_recs = []
                    tail_txt = (m2.group("tail") or "").strip()
                    q_block = None
                    mq = RE_QLABEL.match(tail_txt)
                    if mq and q2_doc:
                        q_block = "Q" + mq.group("q")
                        tail_txt = (mq.group("tail") or "").strip()
                    run_label = None
                    if not q_block and tail_txt.split() and (RE_RUN_NOCOLON.match(tail_txt.split()[0]) or RE_RUNLBL.match(tail_txt.split()[0])):
                        run_label = tail_txt.split()[0].rstrip(":")
                    if not (mq and q2_doc) and tail_txt.split() and RE_RUN_NOCOLON.match(tail_txt.split()[0]):
                        tail_txt = tail_txt.split(" ", 1)[1] if " " in tail_txt else ""
                    rec = _new_record(athlete, pno, q_block, True)
                    rec["section"] = section
                    rec["run_label"] = run_label
                    rec["block_index"] = 0
                    records.append(rec)
                    athlete_recs.append(rec)
                    if not tail_txt:
                        warnings.append((pno, "athlete line without status or scores", l))
                        last_kind = "status"
                        continue
                    if _parse_block_tail(rec, tail_txt, warnings, pno, l):
                        cur, expect, last_kind = rec, "D", "scored"
                    else:
                        last_kind = "status"
                    continue

                # --- further Q block of the same athlete ----------------
                m = RE_QBLOCK.match(l)
                if m and athlete is not None and q2_doc:
                    close_block(pno)
                    rec = _new_record(athlete, pno, "Q" + m.group("q"), False)
                    rec["section"] = section
                    records.append(rec)
                    athlete_recs.append(rec)
                    if _parse_block_tail(rec, m.group("tail"), warnings, pno, l):
                        cur, expect, last_kind = rec, "D", "scored"
                    else:
                        last_kind = "status"
                    continue

                # --- continuation run of the same athlete (overall report) ---
                m = RE_CONT.match(l)
                if m and athlete is not None and expect == "L1" and not q2_doc:
                    close_block(pno)
                    rec = _new_record(athlete, pno, None, True)
                    rec.update({"section": section, "rank": None, "run_label": m.group("lbl"), "block_index": len(athlete_recs)})
                    records.append(rec)
                    athlete_recs.append(rec)
                    if _parse_block_tail(rec, m.group("tail"), warnings, pno, l):
                        cur, expect, last_kind = rec, "D", "scored"
                    else:
                        last_kind = "status"
                    continue

                # --- D line ---------------------------------------------
                m = RE_DLINE.match(l)
                if m:
                    if cur is None or expect != "D":
                        meta["unparsed_lines"].append((pno, l))
                        continue
                    wrap = m.group("wrap")
                    # 走行ラベルが 2 行目（D の行）の先頭にある様式（ANC 2019 の総合「F2: 6.5 5.0 S 0.480 D: …」）
                    lbl = m.group("runlbl")
                    if wrap:
                        # 折り返した名前の後ろに走行ラベル（PH: など）が付く / ラベルだけの行がある（NAC 2022）
                        lbl = lbl or next((x.rstrip(":") for x in wrap.split() if RE_RUNLBL.match(x)), None)
                        wt = [x for x in wrap.split() if not RE_RUNLBL.match(x)]
                        wrap = " ".join(wt)
                    if lbl and cur.get("run_label") is None:
                        cur["run_label"] = lbl
                    if wrap:
                        _append_name(athlete, athlete_recs, wrap)
                    _parse_dline(cur, m, warnings, pno, l)
                    expect, last_kind = "L3", "D"
                    continue

                # --- 3rd line -------------------------------------------
                m = RE_L3.match(l)
                if m:
                    if cur is None or expect != "L3":
                        meta["unparsed_lines"].append((pno, l))
                        continue
                    if m.group("wrap"):  # 3 行目の頭に折り返した名前（世界ジュニア 2017「Kenneth 9.25 11.03 40.10」）
                        _append_name(athlete, athlete_recs, m.group("wrap").strip())
                    _parse_l3(cur, m, warnings, pno, l)
                    cur, expect, last_kind = None, "L1", "L3"
                    continue

                # --- wrapped name fragment after a status row / 3 行目に折り返した名前（D 行と 3 行目の間）
                if athlete is not None and (last_kind == "status" or expect == "L3") \
                        and not RE_DIGIT.search(" ".join(x for x in l.split() if not RE_RUNLBL.match(x))):
                    toks = [x for x in l.split() if not RE_RUNLBL.match(x)]
                    lbls = [x.rstrip(":") for x in l.split() if RE_RUNLBL.match(x)]
                    if lbls and last_kind == "status" and athlete_recs and athlete_recs[-1].get("run_label") is None:
                        # 途中棄権などの行の下に走りの印だけがある（ANC 2019 の総合「DNF 60.90 99」の次の行「F2:」）
                        athlete_recs[-1]["run_label"] = lbls[0]
                        if not toks:
                            continue
                    if toks and len(toks) <= 3 and all(RE_NAME_TOKEN.match(x) and x not in NOT_NAME for x in toks):
                        _append_name(athlete, athlete_recs, " ".join(toks))
                        continue
                    meta["unparsed_lines"].append((pno, l))
                    continue

                meta["unparsed_lines"].append((pno, l))

    close_block(0)

    meta["round"] = meta.get("round_after") or meta.get("round_raw")
    return meta, records


if __name__ == "__main__":  # pragma: no cover
    import json
    import sys

    m, r = parse_moguls_results(sys.argv[1])
    print(json.dumps(m, ensure_ascii=False, indent=1, default=str))
    print(len(r), "records")
    for x in r[:3]:
        print(json.dumps(x, ensure_ascii=False))
