"""
simple_card.py - created 2026-11-13. The plain-language nightly scorecard (the page a retail trader reads).

The nightly replay (scripts/cvx_replay.py) keeps writing its full technical scorecard; this module reads the
files that run already saved and builds one simple page from them:
  1 Is it making money?      the seven practice trades (volume basis = the running basis), seed 10/26-27 + every night
  2 Which trades make money? the same seven, per rule, best first
  3 Which map signals work?  six signals, each next to a simple guess anyone could make
  4 Today                    close / move / expected move, today's checks, one chart
No scoring maths lives here: every number comes from history.csv, <day>/seven_tests.json, <day>/p1.json and
<day>/levels_<ymd>.ndjson as cvx_replay / p1score wrote them.

Rules for the signal cards (fixed 2026-11-13 before any results were read; change only with a dated note):
  levels    "Ours" levels (the 08:35 plan) held / (held + broke), p1score #17 rules; simple guess = the
            "Straddle" levels (open +- AM straddle, prior close +- overnight straddle), same rules.
  range     close inside the 09:31 density cone P10-P90 (history.csv). A good 80% range lands inside ~80%:
            working = 70-90%.
  ceiling   the boundary line the rule says binds (long gamma: vanna upper + gamma lower; short gamma: gamma upper
            + vanna lower), hit = |level - rest-of-day extreme| <= 5 pts after it locked (p1score). Simple guess =
            the prior day's high / low vs today's high / low, same 5 pts, same days.
  calm      regime (p1score: gamma at spot > 0 on >= 50% of columns = long = calm) vs the day's high-low as % of
            the AM straddle (range_pct) against the median range_pct of all nights so far: calm right if <= median,
            wild right if > median. Simple guess = coin flip (50%), since the median splits the nights in half.
  close     the projected 15:50 gamma zero (plain model) at 14:00 vs the 16:00 close, mean miss in pts; simple
            guess = SPX at 14:00 (p1score proj rows, "naive"). Lower is better.
  plan      our 08:35 IF/THEN lines: target hit / line started (p1score road map); simple guess = the
            newsletter's plan graded the same way (when it was read that day).
  Status    fewer than 20 nights -> "too early" (with a lean). Then higher-is-better: map - guess >= 5 pct-pts =
            working, <= 0 = not working, between = too early (too close to call). Lower-is-better (pts): guess - map
            >= 2 = working, <= 0 = not working. Range: 70-90% = working, else not working.

CLI (rebuild any night, e.g. after a fix):  python scripts/cvxmap/simple_card.py --root <replay out root> --day 2026-11-12
"""
import argparse
import csv
import datetime
import json
import statistics as stx
from pathlib import Path

MIN_NIGHTS = 20
HIT_PTS = 5.0

# test id -> (plain name, one line). Ids are cvx_replay.TEST_NAMES.
PLAIN = {
    "1 Fly @ vanna top": ("Butterfly at the top line", "Bets the close ends at the map's top line"),
    "2 Fly @ gamma zero": ("Butterfly at the balance line", "Bets the close ends at the map's middle (balance) line"),
    "3 Fly @ vanna bottom": ("Butterfly at the bottom line", "Bets the close ends at the map's bottom line"),
    "4 Fly @ gamma zero +1SD": ("Butterfly back to the balance line", "Only when price is far away: bets it comes back to the middle line"),
    "5 Credit spread": ("Paid to stay in the box", "Gets paid if SPX stays between the map's top and bottom lines"),
    "6 Debit 1:5": ("Cheap breakout bet", "Small bet on a run above the map's biggest level; aims for 4x"),
    "7 Old fly": ("Old butterfly rule", "The first rule we used, kept for comparison"),
}
BINDS = {"long": ("vanna_upper", "gamma_lower"), "short": ("gamma_upper", "vanna_lower")}


# ------------------------------------------------------------------ small helpers
def _num(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _load(p):
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _mm(t):
    h, m = str(t).split(":")[:2]
    return int(h) * 60 + int(m)


def _iso(dirname):
    return "%s-%s-%s" % (dirname[:4], dirname[4:6], dirname[6:8])


def _md(day_iso):
    d = datetime.date.fromisoformat(day_iso)
    return "%s %d" % (d.strftime("%b"), d.day)


def history(root):
    p = Path(root) / "history.csv"
    if not p.is_file():
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return sorted((dict(r) for r in csv.DictReader(fh) if r.get("date")), key=lambda r: r["date"])


def nights(root, day_iso, name):
    """[(day_iso, obj)] for every <ymd>/<name> up to and including day_iso."""
    out = []
    for p in sorted(Path(root).glob("2*/" + name)):
        d = _iso(p.parent.name)
        if d <= day_iso:
            j = _load(p)
            if j is not None:
                out.append((d, j))
    return out


def status(kind, m, b, n_nights, enough=MIN_NIGHTS):
    """-> (st, lean). st in gain / wait / loss; lean = 'better' / 'worse' / 'same' / None."""
    if m is None:
        return "wait", None
    if kind == "range80":
        ok = 70.0 <= m <= 90.0
        lean = "better" if ok else "worse"
        if n_nights < enough:
            return "wait", lean
        return ("gain" if ok else "loss"), lean
    if b is None:
        return "wait", None
    diff = (m - b) if kind == "pct" else (b - m)
    good, bad = (5.0, 0.0) if kind == "pct" else (2.0, 0.0)
    lean = "better" if diff >= good else ("worse" if diff <= bad else "same")
    if n_nights < enough:
        return "wait", lean
    return {"better": "gain", "worse": "loss", "same": "wait"}[lean], lean


def lean_text(kind, lean, m):
    if m is None:
        return "No result yet"
    if kind == "range80":
        if m > 90:
            return "So far: the range is too wide (a good one misses about 2 days in 10)"
        if m < 70:
            return "So far: the range is too narrow (the close lands outside too often)"
        return "So far: about right"
    return {"better": "So far: better than the simple guess", "worse": "So far: worse than the simple guess",
            "same": "So far: about the same as the simple guess"}.get(lean, "No simple guess to compare yet")


# ------------------------------------------------------------------ 1 + 2: money
def money(root, day_iso, seed, names, book):
    by = {n: [0, 0.0, 0, 0] for n in names}          # n, pl, nightly wins, nightly n
    days = []
    if seed:
        sn = sum(v[0] for v in seed["tests"].values())
        sp = sum(v[2] for v in seed["tests"].values())
        for n, v in seed["tests"].items():
            if n in by:
                by[n][0] += v[0]
                by[n][1] += v[2]
        days.append({"d": "Oct 26–27", "pl": sp, "n": sn, "seed": True})
    today = None
    for d, tj in nights(root, day_iso, "seven_tests.json"):
        if seed and d < seed["since"]:
            continue
        vb = tj.get("vol") or {}
        tot = vb.get("total") or {}
        for s in vb.get("summary") or []:
            a = by.get(s["test"])
            if a is None:
                continue
            a[0] += s.get("n", 0)
            a[1] += s.get("pl", 0)
            a[2] += s.get("wins", 0)
            a[3] += s.get("n", 0)
        row = {"d": _md(d), "iso": d, "pl": tot.get("pl", 0), "n": tot.get("n", 0)}
        days.append(row)
        if d == day_iso:
            today = row
    total = sum(r["pl"] for r in days)
    ntr = sum(r["n"] for r in days)
    nightly = [r for r in days if not r.get("seed")]
    strats = []
    for n in names:
        a = by[n]
        nm, sub = PLAIN.get(n, (n, ""))
        strats.append({"id": n, "name": nm, "sub": sub, "n": a[0], "pl": round(a[1]),
                       "avg": round(a[1] / a[0]) if a[0] else None,
                       "win": round(100.0 * a[2] / a[3]) if a[3] else None, "few": a[0] < 20})
    strats.sort(key=lambda s: -s["pl"])
    return {"total": round(total), "n": ntr, "pct": round(100.0 * total / book, 2) if book else None,
            "today_pl": round(today["pl"]) if today else None, "today_n": today["n"] if today else None,
            "nights": len(nightly), "up_nights": sum(1 for r in nightly if r["pl"] > 0),
            "days": days, "strats": strats, "since": seed["since"] if seed else (days[0]["iso"] if days else day_iso)}


# ------------------------------------------------------------------ 3: signals
def _binding(b):
    """p1 boundary -> {'ceiling': line dict, 'floor': line dict} for the lines the rule says bind."""
    if not b or b.get("regime") not in BINDS:
        return {}
    up, lo = BINDS[b["regime"]]
    L = b.get("lines") or {}
    return {"ceiling": L.get(up), "floor": L.get(lo)}


def signals(root, day_iso, hist=None):
    hist = hist if hist is not None else history(root)
    H = [r for r in hist if r["date"] <= day_iso]
    P1 = nights(root, day_iso, "p1.json")
    out = []

    # levels bounce
    m = {"held": 0, "broke": 0}
    g = {"held": 0, "broke": 0}
    lv_days = set()
    for d, p in P1:
        for r in p.get("levels") or []:
            tgt = m if r.get("src") == "Ours" else g if r.get("src") == "Straddle" else None
            if tgt is not None and r.get("result") in ("held", "broke"):
                tgt[r["result"]] += 1
                if r.get("src") == "Ours":
                    lv_days.add(d)
    nm, ng = m["held"] + m["broke"], g["held"] + g["broke"]
    mv = round(100.0 * m["held"] / nm) if nm else None
    gv = round(100.0 * g["held"] / ng) if ng else None
    st, lean = status("pct", mv, gv, len(lv_days))
    out.append({"key": "levels", "st": st, "lean": lean, "name": "Price bounces at map levels",
                "what": "When price touches one of the map's morning levels, it bounces back 10+ pts before breaking through.",
                "kind": "pct", "map": mv, "base": gv, "baseName": "Expected-move levels",
                "foot": "%d touches · %d nights" % (nm, len(lv_days))})

    # close inside the morning range
    rr = [(r, _num(r.get("cone0_p10")), _num(r.get("cone0_p90")), _num(r.get("close"))) for r in H]
    rr = [(r, a, b, c) for r, a, b, c in rr if None not in (a, b, c)]
    ins = sum(1 for _, a, b, c in rr if a <= c <= b)
    mv = round(100.0 * ins / len(rr)) if rr else None
    st, lean = status("range80", mv, 80, len(rr))
    out.append({"key": "range", "st": st, "lean": lean, "name": "Close lands in the morning range",
                "what": "At 9:31 the map gives a range for the close. A good range is right about 8 days in 10.",
                "kind": "pct", "map": mv, "base": 80, "baseName": "Should be",
                "foot": "%d of %d nights" % (ins, len(rr))})

    # ceiling and floor
    hi_lo = {r["date"]: (_num(r.get("high")), _num(r.get("low"))) for r in H}
    order = [r["date"] for r in H]
    mh = mn = gh = gn = 0
    cf_days = 0
    for d, p in P1:
        bl = _binding(p.get("boundary"))
        scored = [(k, ln) for k, ln in bl.items() if ln and ln.get("err") is not None]
        if not scored:
            continue
        cf_days += 1
        prev = order[order.index(d) - 1] if d in order and order.index(d) > 0 else None
        for k, ln in scored:
            mn += 1
            mh += 1 if abs(ln["err"]) <= HIT_PTS else 0
            if prev and d in hi_lo:
                ph, pl_ = hi_lo[prev]
                th, tl = hi_lo[d]
                a, b = (ph, th) if k == "ceiling" else (pl_, tl)
                if a is not None and b is not None:
                    gn += 1
                    gh += 1 if abs(a - b) <= HIT_PTS else 0
    mv = round(100.0 * mh / mn) if mn else None
    gv = round(100.0 * gh / gn) if gn else None
    st, lean = status("pct", mv, gv, cf_days)
    out.append({"key": "ceiling", "st": st, "lean": lean, "name": "Ceiling and floor",
                "what": "The day's high (or low) ends within 5 pts of the map's ceiling (or floor).",
                "kind": "pct", "map": mv, "base": gv, "baseName": "Yesterday's high/low",
                "foot": "%d of %d levels · %d nights" % (mh, mn, cf_days)})

    # calm or wild
    cw = [(r.get("regime"), _num(r.get("range_pct"))) for r in H]
    cw = [(g_, x) for g_, x in cw if g_ in ("long", "short") and x is not None]
    if cw:
        med = stx.median(x for _, x in cw)
        right = sum(1 for g_, x in cw if (g_ == "long" and x <= med) or (g_ == "short" and x > med))
        mv = round(100.0 * right / len(cw))
    else:
        right, mv = 0, None
    st, lean = status("pct", mv, 50, len(cw))
    out.append({"key": "calm", "st": st, "lean": lean, "name": "Calm day or wild day",
                "what": "The morning call on whether moves snap back (calm) or keep going (wild).",
                "kind": "pct", "map": mv, "base": 50, "baseName": "Coin flip",
                "foot": "%d of %d nights right" % (right, len(cw))})

    # close = balance line (14:00 projection)
    me, ne = [], []
    for d, p in P1:
        for r in p.get("proj") or []:
            if r.get("t") == "14:00" and r.get("plain_gz_err") is not None and r.get("naive") is not None:
                me.append(r["plain_gz_err"])
                ne.append(r["naive"])
    mv = round(sum(me) / len(me)) if me else None
    gv = round(sum(ne) / len(ne)) if ne else None
    st, lean = status("pts", mv, gv, len(me))
    out.append({"key": "close", "st": st, "lean": lean, "name": "Close = the balance line",
                "what": "At 2 pm, using the map's middle line as the guess for the close. Average miss.",
                "kind": "pts", "map": mv, "base": gv, "baseName": "SPX at 2 pm",
                "foot": "%d nights · lower is better" % len(me)})

    # morning game plan
    rm = {"Ours": [0, 0, 0], "Doc": [0, 0, 0]}
    for d, p in P1:
        for lab, key in (("Doc", "doc_grades"), ("Ours", "own_grades")):
            G = [x for x in (p.get(key) or []) if x.get("kind") != "lean"]
            if not G:
                continue
            a = rm[lab]
            a[0] += 1
            a[1] += sum(1 for x in G if x.get("t0") is not None)
            a[2] += sum(1 for x in G if x.get("hit") is not None)
    o, dc = rm["Ours"], rm["Doc"]
    mv = round(100.0 * o[2] / o[1]) if o[1] else None
    gv = round(100.0 * dc[2] / dc[1]) if dc[1] else None
    st, lean = status("pct", mv, gv, o[0])
    out.append({"key": "plan", "st": st, "lean": lean, "name": "Morning game plan",
                "what": "8:35 'if price does X, look for Y' lines: the target is hit after the line starts.",
                "kind": "pct", "map": mv, "base": gv, "baseName": "Newsletter's plan",
                "foot": "%d of %d lines · %d nights" % (o[2], o[1], o[0])})
    for x in out:
        x["lean_text"] = lean_text("range80" if x["key"] == "range" else x["kind"], x["lean"], x["map"])
    return out


# ------------------------------------------------------------------ 4: today
def _hhmm(m):
    h, mi = divmod(int(m), 60)
    ap = "pm" if h >= 12 else "am"
    h = h - 12 if h > 12 else h
    return "%d:%02d %s" % (h, mi, ap)


def today(root, day_iso, hist=None):
    hist = hist if hist is not None else history(root)
    ymd = day_iso.replace("-", "")
    ddir = Path(root) / ymd
    row = next((r for r in hist if r["date"] == day_iso), {})
    close, hi, lo = _num(row.get("close")), _num(row.get("high")), _num(row.get("low"))
    prev = _num(row.get("prev_close"))
    if prev is None:
        earlier = [r for r in hist if r["date"] < day_iso and _num(r.get("close")) is not None]
        prev = _num(earlier[-1]["close"]) if earlier else None
    T = {"close": close, "chg_pct": round(100.0 * (close - prev) / prev, 2) if close and prev else None,
         "moved": round(hi - lo, 1) if hi is not None and lo is not None else None,
         "expected": _num(row.get("am_straddle")), "checks": [], "path": [], "band": None,
         "ceiling": None, "floor": None, "trades": []}
    p1 = _load(ddir / "p1.json") or {}
    b = p1.get("boundary") or {}
    # calm / wild
    rp = _num(row.get("range_pct"))
    reg = row.get("regime") or b.get("regime")
    meds = [_num(r.get("range_pct")) for r in hist if r["date"] <= day_iso]
    meds = [x for x in meds if x is not None]
    if reg in ("long", "short") and rp is not None and meds:
        med = stx.median(meds)
        calm_day = rp <= med
        ok = calm_day if reg == "long" else not calm_day
        said = "calm (moves would snap back)" if reg == "long" else "wild (moves would keep going)"
        was = "calmer" if calm_day else "wilder"
        T["checks"].append({"ok": ok, "title": ("Calm day" if reg == "long" else "Wild day") + (" — right" if ok else " — wrong"),
                            "text": "Map said %s. The day was %s than a normal day." % (said, was)})
    # morning range
    p10, p90 = _num(row.get("cone0_p10")), _num(row.get("cone0_p90"))
    if None not in (p10, p90, close):
        ok = p10 <= close <= p90
        T["band"] = [p10, p90]
        T["checks"].append({"ok": ok, "title": "Close %s the morning range — %s" % ("inside" if ok else "outside", "right" if ok else "wrong"),
                            "text": "9:31 range %s – %s. Closed %s." % (format(round(p10), ","), format(round(p90), ","), format(round(close), ","))})
    # ceiling / floor
    for k, ln in _binding(b).items():
        if not ln or ln.get("level") is None:
            T["checks"].append({"ok": None, "title": "No %s today" % k, "text": "The map never settled on one."})
            continue
        T[k] = round(ln["level"], 1)
        if ln.get("err") is None:
            continue
        ok = abs(ln["err"]) <= HIT_PTS
        ext = "high" if k == "ceiling" else "low"
        T["checks"].append({"ok": ok, "title": "%s — %s" % (k.capitalize(), "right" if ok else "missed"),
                            "text": "%s %s (set at %s). The day's %s after that was %s pts %s it%s." % (
                                k.capitalize(), format(round(ln["level"]), ","), _hhmm(_mm(ln["lock"])) if ln.get("lock") else "–",
                                ext, format(abs(ln["err"]), ".0f"), "below" if ln["err"] > 0 else "above",
                                "" if ok else " (needed 5 or less)")})
    # game plan
    G = [g for g in (p1.get("own_grades") or []) if g.get("kind") != "lean" and g.get("t0") is not None]
    for g in G[:3]:
        ok = g.get("hit") is not None
        T["checks"].append({"ok": ok, "title": "Game plan " + ("hit" if ok else "missed"),
                            "text": "\"%s\": started %s, %s." % (g.get("text", ""), _hhmm(g["t0"]),
                                                                ("target hit " + _hhmm(g["hit"])) if ok else "target not reached")})
    if not G and p1.get("own_grades"):
        T["checks"].append({"ok": None, "title": "Game plan: nothing started", "text": "Price never reached any of the morning's 'if' levels."})
    # price path (2-min columns of the map) and today's trades
    lp = ddir / ("levels_%s.ndjson" % ymd)
    if lp.is_file():
        with open(lp, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("S") and r.get("t"):
                    T["path"].append([_mm(r["t"]), round(float(r["S"]), 2)])
    tj = _load(ddir / "seven_tests.json") or {}
    for t in ((tj.get("vol") or {}).get("trades") or []):
        if "pl" in t and t.get("in") and t.get("test") in PLAIN:
            T["trades"].append({"t": _mm(t["in"]), "pl": t["pl"], "name": PLAIN[t["test"]][0], "spot": t.get("spot")})
    return T


# ------------------------------------------------------------------ assemble
def build(root, day_iso, seed=None, names=None, book=50000.0):
    if names is None or seed is None:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import cvx_replay as R           # stdlib-only at import; gives the locked test names and the 10/26-27 seed
        names = names or R.TEST_NAMES
        seed = seed if seed is not None else R.SEED
        book = R.BOOK
    hist = history(root)
    d = datetime.date.fromisoformat(day_iso)
    M = money(root, day_iso, seed, names, book)
    return {"day": day_iso, "label": d.strftime("%A, %b ") + str(d.day), "since": _md(M["since"]),
            "money": M, "signals": signals(root, day_iso, hist), "today": today(root, day_iso, hist), "min_nights": MIN_NIGHTS}


def page(D):
    data = json.dumps(D, separators=(",", ":")).replace("</", "<\\/")
    return HTML.replace("/*__DATA__*/null", data).replace("__TITLE__", "SPX Map Scorecard · " + D["label"])


def write(root, day_iso, out_path, **kw):
    D = build(root, day_iso, **kw)
    Path(out_path).write_text(page(D), encoding="utf-8")
    return Path(out_path), D


def _money(v):
    return ("+$" if v >= 0 else "−$") + format(abs(round(v)), ",")


def telegram_text(D):
    T, M = D["today"], D["money"]
    d = datetime.date.fromisoformat(D["day"])
    L = ["<b>SPX scorecard · %s</b>" % d.strftime("%a %d %b")]
    if T.get("close"):
        s = "Close %s" % format(round(T["close"]), ",")
        if T.get("chg_pct") is not None:
            s += " (%+.1f%%)" % T["chg_pct"]
        if T.get("moved") is not None:
            s += " · moved %s pts" % format(T["moved"], ".0f")
        if T.get("expected"):
            s += " · options expected ±%s" % format(T["expected"], ".0f")
        L.append(s)
    if M["today_pl"] is not None:
        L.append("Practice trades: today %s (%d) · since %s %s (%d)" % (_money(M["today_pl"]), M["today_n"], D["since"], _money(M["total"]), M["n"]))
    ch = [("✓ " if c["ok"] else "✕ ") + c["title"].split(" — ")[0] for c in T["checks"] if c["ok"] is not None]
    if ch:
        L.append(" · ".join(ch))
    st = [s for s in M["strats"] if s["n"]]
    if st:
        L.append("Best rule so far: %s %s · worst: %s %s" % (st[0]["name"], _money(st[0]["pl"]), st[-1]["name"], _money(st[-1]["pl"])))
    w = [s["name"] for s in D["signals"] if s["st"] == "gain"]
    x = [s["name"] for s in D["signals"] if s["st"] == "loss"]
    if w or x:
        L.append("Working: %s · not working: %s" % (", ".join(w) or "none yet", ", ".join(x) or "none yet"))
    return "\n".join(L)


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--bg:#eef1f5;--card:#ffffff;--ink:#0b0b0b;--ink2:#3d3c39;--line:#d7dce3;--grid:#e8ebef;
  --price:#2a78d6;--band:#cde2fb;--gain:#0f8f84;--loss:#dc3f2c;--wait:#fab219;
  --gainbg:#e3f3f1;--lossbg:#fbe9e6;--waitbg:#fff3d6;--head:#14243a;--headink:#ffffff;
  --s1:#0f8f84;--s2:#2a78d6;--s3:#6250d6;--s4:#14243a}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0f1012;--card:#1a1a19;--ink:#f0efec;--ink2:#c9c8be;--line:#34342f;--grid:#2a2a27;
  --price:#3987e5;--band:#1d3b60;--gain:#14a39a;--loss:#e0533b;--wait:#fab219;
  --gainbg:#10302d;--lossbg:#3a1a14;--waitbg:#3a2f12;--head:#1d3b60;--headink:#ffffff;
  --s1:#14a39a;--s2:#3987e5;--s3:#9085e9;--s4:#5b6b80}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:18px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:780px;margin:0 auto;padding:14px 12px 70px}
.top{background:var(--head);color:var(--headink);border-radius:16px;padding:18px 20px;margin-bottom:14px}
.top .kick{font-size:14px;letter-spacing:.12em;text-transform:uppercase;opacity:.85;font-weight:700}
.top h1{margin:2px 0 4px;font-size:30px;line-height:1.15}
.top p{margin:0;font-size:17px;opacity:.92}
section{background:var(--card);border-radius:16px;padding:18px 18px 16px;margin:14px 0;border-top:6px solid var(--sec)}
.sec1{--sec:var(--s1)}.sec2{--sec:var(--s2)}.sec3{--sec:var(--s3)}.sec4{--sec:var(--s4)}.sec5{--sec:var(--line)}
h2{margin:0 0 4px;font-size:25px;line-height:1.2;display:flex;align-items:center;gap:10px}
h2 .n{flex:none;width:34px;height:34px;border-radius:50%;background:var(--sec);color:#fff;font-size:19px;display:grid;place-items:center}
.lead{margin:0 0 12px;color:var(--ink2);font-size:17px}
.pill{display:inline-flex;align-items:center;gap:6px;font-weight:800;font-size:16px;padding:5px 12px;border-radius:99px;border:2px solid}
.pill.gain{background:var(--gainbg);border-color:var(--gain)}.pill.loss{background:var(--lossbg);border-color:var(--loss)}.pill.wait{background:var(--waitbg);border-color:var(--wait)}
.ic{font-weight:900}.gain .ic{color:var(--gain)}.loss .ic{color:var(--loss)}.wait .ic{color:#b07700}
@media (prefers-color-scheme:dark){.wait .ic{color:var(--wait)}}
.hero{display:flex;flex-wrap:wrap;align-items:flex-end;gap:6px 18px;margin:10px 0 4px}
.hero .big{font-size:52px;font-weight:800;line-height:1;letter-spacing:-.02em}
.hero .what{font-size:18px;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}
.tile{border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.tile .k{font-size:14px;color:var(--ink2);font-weight:600}
.tile .v{font-size:24px;font-weight:800;line-height:1.2}
.tile .s{font-size:14px;color:var(--ink2)}
.note{font-size:15px;color:var(--ink2);margin:8px 0 0}
svg{display:block;width:100%;height:auto;touch-action:pan-y}
svg text{fill:var(--ink2);font-size:14px;font-family:inherit}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:15px;margin:6px 0 2px;color:var(--ink2)}
.legend i{display:inline-block;vertical-align:middle;margin-right:6px}
.brow{padding:10px 0;border-top:1px solid var(--line);cursor:pointer}
.brow:first-child{border-top:0}
.bname{font-weight:700;font-size:18px;display:flex;justify-content:space-between;gap:10px}
.bname .amt{font-variant-numeric:tabular-nums;white-space:nowrap}
.bsub{font-size:15px;color:var(--ink2)}
.btrack{position:relative;height:14px;margin-top:4px}
.btrack .mid{position:absolute;left:50%;top:-3px;bottom:-3px;width:2px;background:var(--ink2);opacity:.5}
.btrack .fill{position:absolute;top:0;height:14px}
.btrack .fill.g{left:50%;background:var(--gain);border-radius:0 4px 4px 0}
.btrack .fill.l{right:50%;background:var(--loss);border-radius:4px 0 0 4px}
.axisrow{display:flex;justify-content:space-between;font-size:13px;color:var(--ink2);margin-top:2px}
.rule{font-size:16px;color:var(--ink2);border-left:4px solid var(--sec);padding:2px 0 2px 10px;margin:0 0 12px}
.grp{margin:14px 0 6px;display:flex;align-items:center;gap:10px}
.grp .cnt{font-size:15px;color:var(--ink2)}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media (max-width:560px){.cards{grid-template-columns:1fr}.hero .big{font-size:44px}}
.card{border:2px solid var(--line);border-left:8px solid var(--c);border-radius:12px;padding:12px 14px;background:var(--cb)}
.card.gain{--c:var(--gain);--cb:var(--gainbg)}.card.loss{--c:var(--loss);--cb:var(--lossbg)}.card.wait{--c:var(--wait);--cb:var(--waitbg)}
.card h3{margin:0 0 2px;font-size:19px}
.card .what{font-size:15px;color:var(--ink2);margin:0 0 8px}
.cmp{display:grid;grid-template-columns:96px 1fr 58px;align-items:center;gap:6px;font-size:15px;margin:3px 0}
.cmp .t{height:12px;background:var(--grid);border-radius:4px;overflow:hidden}
.cmp .t b{display:block;height:12px;border-radius:4px}
.cmp .val{text-align:right;font-weight:800;font-variant-numeric:tabular-nums}
.card .foot{font-size:14px;color:var(--ink2);margin-top:6px}
.card .lean{font-size:15px;font-weight:700;margin-top:4px}
.checks{list-style:none;margin:6px 0 12px;padding:0}
.checks li{display:grid;grid-template-columns:34px 1fr;gap:8px;padding:9px 0;border-top:1px solid var(--line)}
.checks li:first-child{border-top:0}
.mark{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;line-height:1;font-weight:900;color:#fff;font-size:17px}
.mark.y{background:var(--gain)}.mark.n{background:var(--loss)}.mark.z{background:var(--ink2)}
.checks b{font-size:18px}
.checks span.tx{display:block;font-size:15px;color:var(--ink2)}
summary{cursor:pointer;font-weight:700;font-size:18px;padding:4px 0}
dl{margin:6px 0 0}dt{font-weight:800;margin-top:10px}dd{margin:0;color:var(--ink2);font-size:16px}
.foot2{font-size:14px;color:var(--ink2);text-align:center;margin-top:18px}
.empty{font-size:16px;color:var(--ink2);padding:6px 0}
#tip{position:fixed;z-index:9;pointer-events:none;background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;
  padding:8px 11px;font-size:15px;line-height:1.35;box-shadow:0 6px 24px rgba(0,0,0,.18);max-width:260px;display:none}
#tip b{font-size:16px}
.hit{fill:transparent}
</style>
</head>
<body>
<main>
<header class="top">
  <div class="kick">SPX map · nightly scorecard</div>
  <h1 id="hdate"></h1>
  <p>How the cockpit's map did, in plain words. Tap any chart or row for numbers.</p>
</header>

<section class="sec1">
  <h2><span class="n">1</span>Is it making money?</h2>
  <p class="lead" id="mlead"></p>
  <span id="mpill"></span>
  <div class="hero"><span class="big" id="mbig"></span><span class="what" id="mwhat"></span></div>
  <div class="tiles" id="mtiles"></div>
  <svg id="cum" viewBox="0 0 420 200" role="img" aria-label="Running practice profit by day"></svg>
  <div class="legend"><span><i style="width:22px;height:3px;background:var(--gain)"></i>Running total</span><span><i style="width:10px;height:10px;border-radius:50%;background:var(--gain)"></i>Up day</span><span><i style="width:10px;height:10px;border-radius:50%;background:var(--loss)"></i>Down day</span></div>
  <p class="note">Paper trades: 1 contract each, real bid/ask prices, no real money. Not advice.</p>
</section>

<section class="sec2">
  <h2><span class="n">2</span>Which trades make money?</h2>
  <p class="lead">Seven fixed trade rules, best at the top. Green = made money, red = lost money.</p>
  <div id="bars"></div>
  <div class="axisrow"><span>◀ lost money</span><span>made money ▶</span></div>
</section>

<section class="sec3">
  <h2><span class="n">3</span>Which map signals work?</h2>
  <p class="rule">Each signal is checked against a <b>simple guess</b> anyone could make. <b>Working</b> = beats the simple guess over <span class="mn"></span>+ nights. <b>Too early</b> = fewer than <span class="mn"></span> nights, or too close to call.</p>
  <div id="sig"></div>
</section>

<section class="sec4">
  <h2><span class="n">4</span>Today</h2>
  <div class="tiles" id="ttiles"></div>
  <ul class="checks" id="tchecks"></ul>
  <svg id="day" viewBox="0 0 420 300" role="img" aria-label="SPX price today with the map's levels"></svg>
  <div class="legend" id="dlegend">
    <span><i style="width:22px;height:3px;background:var(--price)"></i>SPX price</span>
    <span><i style="width:18px;height:12px;background:var(--band)"></i>Morning close range</span>
    <span><i style="width:22px;border-top:3px dashed var(--ink2)"></i>Ceiling / floor</span>
    <span><i style="width:10px;height:10px;border-radius:50%;background:var(--gain)"></i>Trade won</span>
    <span><i style="width:10px;height:10px;border-radius:50%;background:var(--loss)"></i>Trade lost</span>
  </div>
</section>

<section class="sec5">
  <details>
    <summary>What the words mean</summary>
    <dl>
      <dt>The map</dt><dd>The cockpit's picture of where big option dealers have to buy or sell as SPX moves.</dd>
      <dt>Balance line</dt><dd>The map's middle line. Above it, dealers' hedging tends to calm price; below it, it tends to speed moves up.</dd>
      <dt>Top line / bottom line</dt><dd>The map's outer lines, where price often stalls.</dd>
      <dt>Ceiling / floor</dt><dd>The lines the map expects to stop the day's high and low.</dd>
      <dt>Calm day / wild day</dt><dd>Calm: dealers buy dips and sell rips, so moves snap back. Wild: dealers chase the move, so it keeps going.</dd>
      <dt>Morning close range</dt><dd>At 9:31 the map gives a range where the close should land about 8 days in 10.</dd>
      <dt>Expected move</dt><dd>How far option prices say SPX will move today. Options usually expect more than really happens.</dd>
      <dt>Butterfly</dt><dd>A cheap option bet that pays the most if SPX closes near one price.</dd>
      <dt>Simple guess</dt><dd>What anyone could use without the map: expected-move levels, yesterday's high/low, a coin flip, or today's price.</dd>
    </dl>
  </details>
</section>
<p class="foot2">Practice results on real prices, not advice. The full technical report is kept separately.</p>
</main>
<div id="tip"></div>
<script>
const D=/*__DATA__*/null;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const money=v=>(v>=0?"+$":"−$")+Math.abs(Math.round(v)).toLocaleString("en-US");
const num=v=>Math.round(v).toLocaleString("en-US");
const hhmm=m=>{let h=Math.floor(m/60),mi=m%60,ap=h>=12?"pm":"am";h=h>12?h-12:h;return h+":"+String(mi).padStart(2,"0")+" "+ap;};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const NS="http://www.w3.org/2000/svg";
function el(tag,attrs,parent){const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(parent)parent.appendChild(e);return e;}
const $=id=>document.getElementById(id);
const tip=$("tip");
function showTip(html,x,y){tip.innerHTML=html;tip.style.display="block";const w=tip.offsetWidth,h=tip.offsetHeight;
  let L=x+14,T=y-h-12;if(L+w>innerWidth-8)L=x-w-14;if(L<8)L=8;if(T<8)T=y+16;tip.style.left=L+"px";tip.style.top=T+"px";}
function hideTip(){tip.style.display="none";}
document.addEventListener("pointerdown",e=>{if(!e.target.closest("svg,.brow"))hideTip();});
const OK='<svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M4 10.5l4 4 8-9" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const NO='<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>';
const DASH='<svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true"><path d="M5 10h10" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>';
function tile(k,v,s){return '<div class="tile"><div class="k">'+k+'</div><div class="v">'+v+'</div><div class="s">'+(s||"&nbsp;")+'</div></div>';}
document.querySelectorAll(".mn").forEach(e=>e.textContent=D.min_nights);
$("hdate").textContent=D.label;

// 1 money
(function(){
  const M=D.money;
  $("mlead").textContent="Practice trades that follow the map's rules, every day since "+D.since+".";
  const up=M.total>0, early=M.nights<D.min_nights;
  $("mpill").outerHTML='<span class="pill '+(M.total>0?"gain":M.total<0?"loss":"wait")+'"><span class="ic">'+(up?"▲":M.total<0?"▼":"–")+'</span>'+
    (up?"YES — up overall":M.total<0?"NO — down overall":"Flat so far")+(early?" (early days)":"")+'</span>';
  $("mbig").textContent=money(M.total);
  $("mwhat").textContent="since "+D.since+" · "+M.n+" trades";
  $("mtiles").innerHTML=tile("Today",M.today_pl==null?"–":money(M.today_pl),M.today_pl==null?"no trades file":M.today_n+" trades")+
    tile("Up nights",M.up_nights+" of "+M.nights,M.nights?Math.round(100*M.up_nights/M.nights)+"%":"")+
    tile("Account",M.pct==null?"–":(M.pct>=0?"+":"")+M.pct.toFixed(1)+"%","of $50,000");
  const svg=$("cum"),W=420,H=200,L=66,R=12,T=12,B=30,days=M.days;
  if(days.length<2){el("text",{x:W/2,y:H/2,"text-anchor":"middle"},svg).textContent="Chart starts after two nights";return;}
  let run=0;const cum=days.map(r=>run+=r.pl);
  const lo=Math.min(0,...cum),hi=Math.max(0,...cum),pad=Math.max(50,(hi-lo)*.12);
  const y=v=>T+(H-T-B)*(1-(v-(lo-pad))/((hi+pad)-(lo-pad))),x=i=>L+(W-L-R)*i/(days.length-1);
  const span=(hi+pad)-(lo-pad),step=[100,250,500,1000,2500,5000,10000].find(s=>span/s<=4)||20000;
  for(let v=Math.ceil((lo-pad)/step)*step;v<=hi+pad;v+=step){el("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:css(v===0?"--ink2":"--grid"),"stroke-width":v===0?1.5:1},svg);
    el("text",{x:L-8,y:y(v)+4,"text-anchor":"end"},svg).textContent=v===0?"$0":money(v);}
  el("path",{d:["M",x(0),y(0)].concat(cum.flatMap((v,i)=>["L",x(i),y(v)])).concat(["L",x(cum.length-1),y(0),"Z"]).join(" "),fill:css(run>=0?"--gain":"--loss"),opacity:.12},svg);
  el("path",{d:cum.map((v,i)=>(i?"L":"M")+x(i)+" "+y(v)).join(" "),fill:"none",stroke:css(run>=0?"--gain":"--loss"),"stroke-width":2.5,"stroke-linejoin":"round"},svg);
  const ticks=[0,Math.floor((days.length-1)/2),days.length-1];
  ticks.forEach((i,j)=>{el("text",{x:x(i),y:H-8,"text-anchor":j===0?"start":j===2?"end":"middle"},svg).textContent=days[i].d;});
  const cross=el("line",{y1:T,y2:H-B,stroke:css("--ink2"),"stroke-width":1,opacity:0},svg);
  cum.forEach((v,i)=>el("circle",{cx:x(i),cy:y(v),r:days.length>40?3:5,fill:css(days[i].pl>=0?"--gain":"--loss"),stroke:css("--card"),"stroke-width":2},svg));
  const hit=el("rect",{x:L,y:T,width:W-L-R,height:H-T-B,class:"hit"},svg);
  const pick=e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)*W/r.width;const i=Math.max(0,Math.min(cum.length-1,Math.round((px-L)/(W-L-R)*(cum.length-1))));
    cross.setAttribute("x1",x(i));cross.setAttribute("x2",x(i));cross.setAttribute("opacity",.6);
    showTip("<b>"+days[i].d+"</b><br>"+(days[i].seed?"Those two days: ":"That day: ")+money(days[i].pl)+" ("+days[i].n+" trades)<br>Running total: "+money(cum[i]),e.clientX,e.clientY);};
  hit.addEventListener("pointermove",pick);hit.addEventListener("pointerdown",pick);
  hit.addEventListener("pointerleave",()=>{cross.setAttribute("opacity",0);hideTip();});
})();

// 2 strategies
(function(){
  const box=$("bars"),S=D.money.strats,mx=Math.max(1,...S.map(s=>Math.abs(s.pl)));
  S.forEach(s=>{
    const d=document.createElement("div");d.className="brow";
    const w=(Math.abs(s.pl)/mx*50).toFixed(1);
    const bits=[s.n+" trades"];if(s.win!=null)bits.push("wins "+s.win+"%");if(s.avg!=null)bits.push("avg "+money(s.avg));if(s.few)bits.push("few trades so far");
    d.innerHTML='<div class="bname"><span>'+esc(s.name)+'</span><span class="amt">'+(s.pl>=0?'<span style="color:var(--gain)">▲</span> ':'<span style="color:var(--loss)">▼</span> ')+money(s.pl)+'</span></div>'+
      '<div class="bsub">'+esc(s.sub)+'</div><div class="bsub">'+bits.join(" · ")+'</div>'+
      '<div class="btrack"><span class="mid"></span><span class="fill '+(s.pl>=0?"g":"l")+'" style="width:'+w+'%"></span></div>';
    const t=e=>showTip("<b>"+esc(s.name)+"</b><br>"+s.n+" trades"+(s.win!=null?" · wins "+s.win+"% (since Oct 28)":"")+"<br>Total "+money(s.pl)+(s.avg!=null?"<br>Average "+money(s.avg)+" per trade":""),e.clientX,e.clientY);
    d.addEventListener("pointermove",t);d.addEventListener("pointerdown",t);d.addEventListener("pointerleave",hideTip);
    box.appendChild(d);
  });
})();

// 3 signals
(function(){
  const box=$("sig");
  [["gain","✓","Working"],["wait","⏳","Too early to tell"],["loss","✕","Not working"]].forEach(([st,ic,label])=>{
    const list=D.signals.filter(s=>s.st===st);if(!list.length)return;
    const g=document.createElement("div");g.className="grp";
    g.innerHTML='<span class="pill '+st+'"><span class="ic">'+ic+'</span>'+label+'</span><span class="cnt">'+list.length+" signal"+(list.length>1?"s":"")+'</span>';
    box.appendChild(g);
    const cards=document.createElement("div");cards.className="cards";box.appendChild(cards);
    list.forEach(s=>{
      const unit=s.kind==="pct"?"%":" pts",vals=[s.map,s.base].filter(v=>v!=null),top=s.kind==="pct"?100:Math.max(1,...vals)*1.15;
      const bar=(v,c,o)=>v==null?'<span class="t"></span><span class="val">–</span>':'<span class="t"><b style="width:'+Math.min(100,v/top*100)+'%;background:'+c+';opacity:'+o+'"></b></span><span class="val">'+v+unit+'</span>';
      const lean=st==="wait"&&s.lean_text?'<div class="lean">'+esc(s.lean_text)+'</div>':"";
      const c=document.createElement("div");c.className="card "+st;
      c.innerHTML='<h3>'+esc(s.name)+'</h3><p class="what">'+esc(s.what)+'</p>'+
        '<div class="cmp"><span><b>Map</b></span>'+bar(s.map,"var(--c)",1)+'</div>'+
        '<div class="cmp"><span>'+esc(s.baseName)+'</span>'+bar(s.base,"var(--ink2)",.55)+'</div>'+lean+
        '<div class="foot">'+esc(s.foot)+'</div>';
      cards.appendChild(c);
    });
  });
})();

// 4 today
(function(){
  const T=D.today;
  $("ttiles").innerHTML=tile("Close",T.close==null?"–":num(T.close),T.chg_pct==null?"":(T.chg_pct>=0?"+":"")+T.chg_pct.toFixed(1)+"%")+
    tile("Moved",T.moved==null?"–":Math.round(T.moved)+" pts","low to high")+
    tile("Expected",T.expected==null?"–":"±"+Math.round(T.expected),"pts, by options");
  $("tchecks").innerHTML=T.checks.length?T.checks.map(c=>'<li><span class="mark '+(c.ok===true?"y":c.ok===false?"n":"z")+'" aria-label="'+(c.ok===true?"right":c.ok===false?"wrong":"no call")+'">'+(c.ok===true?OK:c.ok===false?NO:DASH)+'</span><div><b>'+esc(c.title)+'</b><span class="tx">'+esc(c.text)+'</span></div></li>').join(""):'<li><span></span><div class="empty">No checks today.</div></li>';
  const svg=$("day"),P=T.path;
  if(P.length<5){svg.outerHTML='<div class="empty">No price chart for today.</div>';$("dlegend").remove();return;}
  const W=420,H=300,L=48,R=62,Tp=12,B=30;
  const ys=P.map(p=>p[1]).concat(T.band||[]).concat([T.ceiling,T.floor].filter(v=>v!=null));
  const lo=Math.min(...ys)-5,hi=Math.max(...ys)+5,m0=Math.min(570,P[0][0]),m1=Math.max(960,P[P.length-1][0]);
  const x=m=>L+(W-L-R)*(m-m0)/(m1-m0),y=v=>Tp+(H-Tp-B)*(1-(v-lo)/(hi-lo));
  if(T.band){el("rect",{x:L,y:y(T.band[1]),width:W-L-R,height:y(T.band[0])-y(T.band[1]),fill:css("--band"),opacity:.6},svg);
    el("text",{x:W-R+6,y:y(T.band[1])+14,style:"font-size:13px"},svg).textContent="Close";
    el("text",{x:W-R+6,y:y(T.band[1])+28,style:"font-size:13px"},svg).textContent="range";}
  const step=[5,10,20,25,50,100].find(s=>(hi-lo)/s<=7)||200;
  for(let v=Math.ceil(lo/step)*step;v<=hi;v+=step){el("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:css("--grid")},svg);
    el("text",{x:L-6,y:y(v)+4,"text-anchor":"end"},svg).textContent=num(v);}
  [[T.ceiling,"Ceiling"],[T.floor,"Floor"]].forEach(([v,lab])=>{if(v==null)return;
    el("line",{x1:L,x2:W-R,y1:y(v),y2:y(v),stroke:css("--ink2"),"stroke-width":2,"stroke-dasharray":"7 5"},svg);
    el("text",{x:W-R+6,y:y(v)+4,style:"font-weight:700;fill:"+css("--ink")},svg).textContent=lab;});
  [570,690,810,960].forEach((m,j)=>{if(m<m0||m>m1)return;el("text",{x:x(m),y:H-8,"text-anchor":j===0?"start":j===3?"end":"middle"},svg).textContent=hhmm(m).replace(":00","");});
  el("path",{d:P.map((q,i)=>(i?"L":"M")+x(q[0]).toFixed(1)+" "+y(q[1]).toFixed(1)).join(" "),fill:"none",stroke:css("--price"),"stroke-width":2.2,"stroke-linejoin":"round"},svg);
  const last=P[P.length-1];el("circle",{cx:x(last[0]),cy:y(last[1]),r:5,fill:css("--price")},svg);
  const pAt=m=>{let best=P[0];for(const q of P){if(q[0]<=m)best=q;else break;}return best[1];};
  T.trades.forEach(t=>el("circle",{cx:x(t.t),cy:y(t.spot!=null?t.spot:pAt(t.t)),r:7,fill:css(t.pl>=0?"--gain":"--loss"),stroke:css("--card"),"stroke-width":2.5},svg));
  const cross=el("line",{y1:Tp,y2:H-B,stroke:css("--ink2"),"stroke-width":1,opacity:0},svg);
  const hit=el("rect",{x:L,y:Tp,width:W-L-R,height:H-Tp-B,class:"hit"},svg);
  const pick=e=>{const r=svg.getBoundingClientRect(),px=(e.clientX-r.left)*W/r.width;
    const m=Math.max(m0,Math.min(m1,Math.round(m0+(px-L)/(W-L-R)*(m1-m0))));
    cross.setAttribute("x1",x(m));cross.setAttribute("x2",x(m));cross.setAttribute("opacity",.6);
    let h="<b>"+hhmm(m)+"</b> · SPX "+pAt(m).toFixed(1);
    T.trades.filter(t=>Math.abs(t.t-m)<=4).forEach(t=>{h+="<br>"+(t.pl>=0?"✓ Won ":"✕ Lost ")+money(t.pl)+" · "+esc(t.name);});
    showTip(h,e.clientX,e.clientY);};
  hit.addEventListener("pointermove",pick);hit.addEventListener("pointerdown",pick);
  hit.addEventListener("pointerleave",()=>{cross.setAttribute("opacity",0);hideTip();});
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="Plain-language nightly scorecard from the replay's saved files.")
    ap.add_argument("--root", required=True, help="replay output root (the folder with history.csv and <ymd>/ folders)")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", help="output html (default <root>/<ymd>/scorecard_<ymd>.html)")
    a = ap.parse_args()
    ymd = a.day.replace("-", "")
    out = Path(a.out) if a.out else Path(a.root) / ymd / ("scorecard_%s.html" % ymd)
    p, D = write(a.root, a.day, out)
    print("wrote %s" % p)
    print(telegram_text(D))


if __name__ == "__main__":
    main()
