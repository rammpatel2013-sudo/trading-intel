#!/usr/bin/env python3
"""zone_cards.py - created 2026-11-22. Z-zone cards for the vol / correlation gauges -> zonecards.json + zones.html.

One card per gauge (VIX, VVIX, implied correlation, VIX curve, MOVE, IV/RV ...). Each card shows how far the gauge
sits from its own 50-day range (z = (value - 50d mean) / 50d sd), the last year of that z with +/-2 sigma zones, and
what SPX did over the next 20 sessions the past times the gauge entered the same zone (since 2014), against any 20
sessions. The history line says whether the zone ever carried an edge - most do not, and the card says so.
Layout + colours: project doc claude/zone-card-style-20261122.md (Mithil's 11/22 factor-card screenshot).

Called from scripts/fuse_tab.py after the Fuse score (same Cboe / FMP / FRED panel, no extra downloads):
    zone_cards.write(dates, X, DATA_DIR)
Descriptor only (CLAUDE.md rule 4) - a zone is a setup or a warning, never a trade by itself. No LLM, no Convex.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

WIN = 50            # z window (sessions)
FWD = 20            # outcome horizon (sessions)
COOL = 10           # sessions between two counted entries into a zone
DROP = 5.0          # "big drop" = SPX falls 5%+ below the day's close at some point in the next 20 sessions
CHART_DAYS = 252
ZONE, NEAR = 2.0, 1.5

# id, title, what a HIGH reading means, what a LOW reading means
GAUGES = [
    ("VIX", "VIX", "Index fear priced high", "Index fear priced low"),
    ("VVIX", "VVIX", "VIX options bid: traders pay up for a vol spike", "Nobody paying for a vol spike"),
    ("COR1M", "Implied correlation 1M", "Stocks priced to move together: stress reaching the index",
     "Stocks priced to move apart: index calm can hide single-stock risk"),
    ("COR3M", "Implied correlation 3M", "Longer-dated correlation bid", "Longer-dated correlation cheap"),
    ("EQ_RATIO", "VIXEQ / VIX", "Stocks wild under a calm index (fuse loading)",
     "Index catching single-stock vol (correlation bid)"),
    ("CURVE", "VIX / VIX3M", "VIX curve flat or inverted: stress now", "Steep calm curve"),
    ("FRONT", "VIX9D / VIX", "Front panic: next 9 days priced above 30 days", "Front calm"),
    ("VV_RATIO", "VVIX / VIX", "Crash insurance bought while VIX is low", "Vol-of-vol lagging the VIX level"),
    ("CSKEW", "COR90D / COR10D", "Puts priced as a crash together, calls as no rally together",
     "Upside and downside correlation alike"),
    ("MOVE", "MOVE (rates vol)", "Rates market unstable", "Rates market calm"),
    ("SKEW", "Cboe SKEW", "Far-out crash puts expensive", "Crash puts cheap"),
    ("IVRV", "VIX / 20d realized", "Index options rich vs what SPX has done", "Options cheap vs recent moves"),
]


# ---------------------------------------------------------------- maths (pure: lists in, lists out)
def _series(X: dict) -> dict:
    """The gauge series aligned on X's dates (None where an input is missing)."""
    n = len(X["VIX"])
    div = lambda a, b: [x / y if x is not None and y not in (None, 0) else None for x, y in zip(a, b)]  # noqa: E731
    spx = X["SPX"]
    rv = [None] * n
    for t in range(20, n):
        r = [math.log(spx[i] / spx[i - 1]) for i in range(t - 19, t + 1)]
        rv[t] = math.sqrt(sum(x * x for x in r) / 20 * 252) * 100
    out = {k: X.get(k) for k in ("VIX", "VVIX", "COR1M", "COR3M", "MOVE", "SKEW")}
    out["EQ_RATIO"] = div(X["VIXEQ"], X["VIX"])
    out["CURVE"] = div(X["VIX"], X["VIX3M"])
    out["FRONT"] = div(X["VIX9D"], X["VIX"])
    out["VV_RATIO"] = div(X["VVIX"], X["VIX"])
    out["CSKEW"] = div(X["COR90D"], X["COR10D"])
    out["IVRV"] = div(X["VIX"], rv)
    return out


def zscores(x: list) -> tuple[list, list]:
    """z of each value vs the previous WIN values (current excluded); also the 50-day mean."""
    z, mu = [None] * len(x), [None] * len(x)
    for t in range(WIN, len(x)):
        w = x[t - WIN:t]
        if x[t] is None or any(v is None for v in w):
            continue
        m = sum(w) / WIN
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / WIN)
        if sd > 0:
            z[t], mu[t] = (x[t] - m) / sd, m
    return z, mu


def outcomes(spx: list) -> list:
    """Per day: (20d return %, big drop within 20d?) or None near the end."""
    n, out = len(spx), []
    for t in range(n):
        if t + FWD >= n:
            out.append(None)
            continue
        low = min(spx[t + 1:t + FWD + 1])
        out.append(((spx[t + FWD] / spx[t] - 1) * 100, (low / spx[t] - 1) * 100 <= -DROP))
    return out


def entries(z: list, side: int, level: float) -> list:
    """Days z first crosses into the zone (side +1 = above +level, -1 = below -level), COOL sessions apart."""
    hit, last = [], -10**9
    for t in range(1, len(z)):
        a, b = z[t - 1], z[t]
        if a is None or b is None:
            continue
        if side * b >= level > side * a and t - last >= COOL:
            hit.append(t)
            last = t
    return hit


def stats(idx: list, oc: list) -> dict | None:
    rows = [oc[t] for t in idx if oc[t] is not None]
    if not rows:
        return None
    n = len(rows)
    return {"n": n, "up": round(100 * sum(r[0] > 0 for r in rows) / n, 1),
            "avg": round(sum(r[0] for r in rows) / n, 2), "drop": round(100 * sum(r[1] for r in rows) / n, 1)}


def record(ep: dict | None, base: dict) -> tuple[str, str]:
    """('strong'|'some'|'weak', which outcome) from a binomial z of the zone's rate vs the any-20-days rate."""
    if not ep or ep["n"] < 12:
        return "weak", ""
    best, which = 0.0, ""
    for key in ("drop", "up"):
        p0 = base[key] / 100
        if 0 < p0 < 1:
            zz = (ep[key] / 100 - p0) / math.sqrt(p0 * (1 - p0) / ep["n"])
            if abs(zz) > abs(best):
                best, which = zz, key
    if abs(best) >= 2.5 and ep["n"] >= 20:
        return "strong", which
    if abs(best) >= 1.8:
        return "some", which
    return "weak", which


def card(gid: str, title: str, hi: str, lo: str, dates: list, x: list, oc: list, base: dict) -> dict | None:
    z, mu = zscores(x)
    last = max((t for t in range(len(z)) if z[t] is not None), default=None)
    if last is None:
        return None
    zn = z[last]
    side = 1 if zn >= 0 else -1
    level = ZONE if abs(zn) >= ZONE else NEAR
    state = ("zone" if abs(zn) >= ZONE else "near" if abs(zn) >= NEAR else "none")
    ep = stats(entries(z, side, level if state != "none" else ZONE), oc)
    rec, which = record(ep, base)
    ago = last - 10 if last >= 10 else 0
    t0 = max(0, last - CHART_DAYS + 1)
    return {"id": gid, "title": title, "asof": dates[last], "value": round(x[last], 3), "mean50": round(mu[last], 3),
            "z": round(zn, 2), "z_2wk": None if z[ago] is None else round(z[ago], 2), "side": side, "state": state,
            "level": level if state != "none" else ZONE, "meaning": hi if side > 0 else lo, "record": rec,
            "record_on": which, "hist": ep, "base": base,
            "chart": {"d": dates[t0:last + 1], "z": [None if v is None else round(v, 2) for v in z[t0:last + 1]]}}


def build(dates: list, X: dict) -> dict:
    oc = outcomes(X["SPX"])
    allx = [r for r in oc if r is not None]
    base = {"n": len(allx), "up": round(100 * sum(r[0] > 0 for r in allx) / len(allx), 1),
            "avg": round(sum(r[0] for r in allx) / len(allx), 2),
            "drop": round(100 * sum(r[1] for r in allx) / len(allx), 1)}
    S = _series(X)
    cards = []
    for gid, title, hi, lo in GAUGES:
        if S.get(gid) is None:
            continue
        c = card(gid, title, hi, lo, dates, S[gid], oc, base)
        if c:
            cards.append(c)
    order = {"zone": 0, "near": 1, "none": 2}
    cards.sort(key=lambda c: (order[c["state"]], -abs(c["z"])))
    return {"asof": dates[-1], "start": dates[WIN], "base": base, "cards": cards,
            "rule": f"z vs prior {WIN} sessions; zones +/-{ZONE} sigma, nearing +/-{NEAR}; outcome = SPX next {FWD} "
                    f"sessions; big drop = {DROP:.0f}%+ below the day's close; entries {COOL}+ sessions apart",
            "built": datetime.now().isoformat(timespec="seconds")}


def write(dates: list, X: dict, data_dir: Path, fuse: dict | None = None) -> dict:
    """Build the cards, write zonecards.json + zones.html into data_dir (atomic), return the dict."""
    from zone_cards_html import page  # same folder (scripts/)

    o = build(dates, X)
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, text in (("zonecards.json", json.dumps(o, separators=(",", ":"))), ("zones.html", page(o, fuse))):
        tmp = data_dir / (name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        tmp.replace(data_dir / name)
    return o
