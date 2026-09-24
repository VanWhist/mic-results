"""Parser B: text-line based parser for FIS moguls single-run result PDFs.

Independent verification parser. It must NOT share code with parser_a.
Only ``pdfplumber.Page.extract_text()`` lines + regular expressions are used
(no word coordinates).

Public API::

    meta, records = parse_moguls_results(path)
"""

import re

import pdfplumber

PARSER_VERSION = "B-1.0"

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
RE_QLABEL = re.compile(r"^Q(?P<q>[12])(?: (?P<tail>.*))?$")
# second (or later) score block of a Q-layout athlete
RE_QBLOCK = re.compile(r"^Q(?P<q>[12]) (?P<tail>.+)$")
# scored tail: seconds timepoints J6 J7 jump DD B: J1..J5 BaseTotal RunScore [extras]
RE_SCORED = re.compile(
    r"^(?P<sec>\d+\.\d\d) (?P<tp>\d+\.\d\d) (?P<j6>\d+\.\d) (?P<j7>\d+\.\d) "
    r"(?P<jump>[0-9A-Za-z]+) (?P<dd>\d\.\d\d) B: (?P<rest>.+)$"
)
# D line: [wrapped name tokens] J6 J7 jump DD D: deductions
RE_DLINE = re.compile(
    r"^(?:(?P<wrap>[^\d\s][^\d]*?) )?(?P<j6>\d+\.\d) (?P<j7>\d+\.\d) "
    r"(?P<jump>[0-9A-Za-z]+) (?P<dd>\d\.\d\d) D:(?P<rest>.*)$"
)
RE_DED = re.compile(r"-\d+\.\d")
# 3rd line: time points, air total, turns total
RE_L3 = re.compile(r"^(?P<tp>\d+\.\d\d) (?P<air>\d+\.\d\d) (?P<turns>\d+\.\d)$")
RE_NUM = re.compile(r"^\d+\.\d+$")
RE_DIVIDER = re.compile(r"^(Qualified to |Not Qualified|Qualified$)")
RE_DIGIT = re.compile(r"\d")

# ---------------------------------------------------------------------------
# Header / footer / jury regexes
# ---------------------------------------------------------------------------
RE_FOOTER_DATE = re.compile(
    r"^(?P<date>\d{1,2} [A-Z]{3} \d{4}) / (?P<body>.+?) / (?P<codex>\d+)(?: Report [Cc]reated .*)?$"
)
RE_FOOTER_OTHER = re.compile(r"^(Report [Cc]reated|www\.fis-ski\.com|Timing/Scoring|Page \d+/\d+|FRS[A-Z]+-)")
RE_JURY_START = re.compile(r"^Jury Technical Data")
RE_JURY_END = re.compile(r"^(Forerunners:|Conditions on course:|Legend:|Progression|Note:|Turnsscore|Timepoints)")

RE_STD_DATE = re.compile(r"^(?P<date>" + WEEKDAY + r" \d{1,2} [A-Z]{3} \d{4}) Start Time: (?P<time>\S+)")
RE_OLY_DATE = re.compile(r"^(?P<date>" + WEEKDAY + r" \d{1,2} [A-Z]{3} \d{4}) (?P<round>.+)$")
RE_OLY_START = re.compile(r"^Start Time (?P<time>\d{1,2}:\d{2})")
RE_RESULTS_ROUND = re.compile(r"^Results (?P<round>.+)$")
RE_AFTER_Q = re.compile(r"^After (?P<round>Qualification \d)$")
RE_NUM_COMP = re.compile(r"^Number of Competitors: (?P<n>\d+)")
RE_EVENT = re.compile(r"((?:Men|Women)'s Moguls)")
RE_VENUE_HDR = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý0-9 .'/\-]+ \([A-Z]{3}\)$")
RE_HEADER = re.compile(
    r"^(FIS .*(?:WORLD CUP|World Cup|Championships|CHAMPIONSHIPS)|Results\b|MO$|Number of Competitors|Time Air Turns|Rank Bib|Points$|"
    r"After Qualification|Start Time |\S+/\S+$|.*Freestyle Skiing$|.*(?:Men|Women)'s Moguls$)"
)

# technical data suffixes (appear at the end of jury lines)
RE_TECH = [
    ("course_name", re.compile(r" ?Course Name (?P<v>.+)$")),
    ("course_length_m", re.compile(r" ?Length (?P<v>[\d.]+)m$")),
    ("course_gate_width", re.compile(r" ?Course / Gate Width (?P<v>[\d.]+m / [\d.]+m)$")),
    ("gradient_deg", re.compile(r" ?Gradient (?P<v>[\d.]+)°$")),
    ("pace_time", re.compile(r" ?Pace Time (?P<v>[\d.]+)s?$")),
    ("homologation", re.compile(r" ?Homologation Number (?P<v>\S+)$")),
    ("_judges_hdr", re.compile(r" ?Judges$")),
]
RE_JUDGE = re.compile(r"(?:^| )Judge (?P<no>\d) \((?P<role>[^)]+)\) (?P<rest>.+)$")
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
    if tokens and tokens[0] in STATUS_WORDS:
        rec["status"] = tokens[0]
        _apply_extras(rec, tokens[1:], warnings, page, line)
        return False
    m = RE_SCORED.match(tail)
    if not m:
        warnings.append((page, "unrecognised score tail", line))
        return True
    rec["seconds"] = _f(m.group("sec"))
    rec["time_points"] = _f(m.group("tp"))
    rec["air_jumps"].append({"J6": _f(m.group("j6")), "J7": _f(m.group("j7")),
                             "jump": m.group("jump"), "DD": _f(m.group("dd"))})
    rest = m.group("rest").split()
    if len(rest) < 7:
        warnings.append((page, "short B: line", line))
        return True
    rec["base_scores"] = [_f(x) for x in rest[:5]]
    rec["base_total"] = _f(rest[5])
    rec["run_score"] = _f(rest[6])
    _apply_extras(rec, rest[7:], warnings, page, line)
    return True


def _apply_extras(rec, extras, warnings, page, line):
    """Handle the optional trailing tokens: best score (Q-layout), Tie, RES."""
    nums = []
    for t in extras:
        if t == "RES":
            rec["reserve_judge"] = True
        elif RE_NUM.match(t):
            nums.append(float(t))
        else:
            warnings.append((page, "unexpected trailing token %r" % t, line))
    if rec["q_block"] is not None and rec["counting"] and nums:
        rec["best_score"] = nums.pop(0)
    if nums:
        rec["tie"] = nums.pop(0)
    if nums:
        warnings.append((page, "extra numeric tokens %r" % nums, line))


def _parse_dline(rec, m, warnings, page, line):
    rec["air_jumps"].append({"J6": _f(m.group("j6")), "J7": _f(m.group("j7")),
                             "jump": m.group("jump"), "DD": _f(m.group("dd"))})
    deds = [float(x) for x in RE_DED.findall(m.group("rest"))]
    if len(deds) == 6:
        rec["ded_scores"] = deds[:5]
        rec["ded_total"] = deds[5]
    elif len(deds) == 5:
        rec["ded_scores"] = deds
        # the total column is blank when every deduction is -0.0
        rec["ded_total"] = 0.0
        if any(d != 0.0 for d in deds):
            warnings.append((page, "5 deductions but not all zero", line))
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

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            lines = [l.rstrip() for l in text.split("\n") if l.strip()]
            skip_rest = False      # after Forerunners/Conditions/Legend until footer

            for l in lines:
                if _parse_footer_line(l, meta) or RE_FOOTER_OTHER.match(l):
                    skip_rest = False
                    in_jury = False
                    continue
                if _is_header_line(l):
                    _parse_header_line(l, meta)
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
                    if m2:
                        yb = int(m2.group("yb"))
                    else:
                        m2 = RE_NAME_NOC_NOYB.match(rest)
                    if not m2:
                        meta["unparsed_lines"].append((pno, l))
                        continue
                    athlete = {
                        "rank": int(m.group("rank")) if m.group("rank") else None,
                        "bib": int(m.group("bib")),
                        "fis_code": m.group("code"),
                        "name": m2.group("name").strip(),
                        "noc": m2.group("noc"),
                        "yb": yb,
                    }
                    athlete_recs = []
                    tail_txt = (m2.group("tail") or "").strip()
                    q_block = None
                    mq = RE_QLABEL.match(tail_txt)
                    if mq:
                        q_block = "Q" + mq.group("q")
                        tail_txt = (mq.group("tail") or "").strip()
                    rec = _new_record(athlete, pno, q_block, True)
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
                if m and athlete is not None:
                    close_block(pno)
                    rec = _new_record(athlete, pno, "Q" + m.group("q"), False)
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
                    if m.group("wrap"):
                        _append_name(athlete, athlete_recs, m.group("wrap"))
                    _parse_dline(cur, m, warnings, pno, l)
                    expect, last_kind = "L3", "D"
                    continue

                # --- 3rd line -------------------------------------------
                m = RE_L3.match(l)
                if m:
                    if cur is None or expect != "L3":
                        meta["unparsed_lines"].append((pno, l))
                        continue
                    _parse_l3(cur, m, warnings, pno, l)
                    cur, expect, last_kind = None, "L1", "L3"
                    continue

                # --- wrapped name fragment after a status row -----------
                if athlete is not None and last_kind == "status" and not RE_DIGIT.search(l):
                    _append_name(athlete, athlete_recs, l.strip())
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
