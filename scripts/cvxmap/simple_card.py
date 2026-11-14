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
import sys
from pathlib import Path

import simple_card_html as H   # 2026-11-14: the page is written in Python (no scripts needed to see it)

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
    """2026-11-14: finished HTML from simple_card_html (was a script-drawn page that phones showed empty)."""
    return H.render(D)


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


def main():
    ap = argparse.ArgumentParser(description="Plain-language nightly scorecard from the replay's saved files.")
    ap.add_argument("--root", required=True, help="replay output root (the folder with history.csv and <ymd>/ folders)")
    ap.add_argument("--day", required=True, help="YYYY-MM-DD")
    ap.add_argument("--out", help="output html (default <root>/<ymd>/scorecard_<ymd>.html)")
    ap.add_argument("--send", action="store_true", help="also send the text + page to Telegram (2026-11-14, for re-sends)")
    a = ap.parse_args()
    ymd = a.day.replace("-", "")
    out = Path(a.out) if a.out else Path(a.root) / ymd / ("scorecard_%s.html" % ymd)
    p, D = write(a.root, a.day, out)
    print("wrote %s" % p)
    print(telegram_text(D))
    if a.send:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from trading_intel.clients.telegram import TelegramClient
        from trading_intel.config import Settings
        tc = TelegramClient(Settings())
        ok = tc.enabled and tc.send_message(telegram_text(D)) and tc.send_document(p, caption="SPX scorecard %s" % a.day)
        print("telegram: %s" % ("sent" if ok else "NOT sent"))


if __name__ == "__main__":
    main()
