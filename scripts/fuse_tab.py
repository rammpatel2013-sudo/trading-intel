#!/usr/bin/env python3
"""fuse_tab.py - created 2026-11-22. Nightly "Fuse" cockpit tab -> scripts/cvxmap/data/fusetab.json (+ fuse.html).

The Correlation Fuse: index vol = average single-stock vol x sqrt(correlation). When stocks stop cancelling each
other (correlation is bid) a calm index turns violent. 22 points in three scores, every one from free end-of-day data:
  Fragility (8)    is the fuse loaded?    very low correlation, stocks wild under a calm index, credit / rates leaking,
                                          crash insurance bought, upside correlation gone, VIX over-reacting to SPX
  Transmission (7) is the move happening? correlation bid, VIX curve inverted, VIX itself unstable, call-side corr bid
  Release (7)      is the move ending?    correlation rolling over, front panic fading, vol-of-vol not confirming,
                                          credit calm, call-side corr fading (counts only during / right after stress)
Phase: Transmission 3+ -> TRANSMITTING (RELEASING if Release 4+); Release 4+ within 10 sessions of stress ->
RELEASING; Fragility 3+ -> LOADED; else QUIET. Every point carries what followed when it was on vs off since 2014.
Rules and the 2014-2026 study: project doc claude/corr-fuse-score-20261122.md (board v2 there).

Data (zero Convex calls, CLAUDE.md rule 7 n/a - no LLM):
  Cboe daily history CSVs (free; answer 200 from the NAS)  COR1M COR3M COR10D COR90D VIXEQ VIX VIX9D VIX3M VVIX SPX SKEW
  FMP via CVForge (fallback per series, same closes)        ^COR1M ... ^GSPC, and ^MOVE (FMP only)
  FRED API                                                  BAMLH0A0HYM2 HY OAS (ICE: last 3 years only)
Descriptor only (rule 4) - not a trade signal.

Run (NAS): bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh fuse_tab
Flags: --out PATH   --no-page (skip copying fuse.html)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = REPO / "scripts" / "cvxmap" / "data"
PAGE_SRC = REPO / "scripts" / "cvxmap" / "fuse.html"
UA = {"User-Agent": "Mozilla/5.0 (trading-intel fuse_tab)"}
START = "2014-06-19"          # VIXEQ's first day = the study start
SERIES_DAYS = 1260            # ~5 years of daily rows in the json (6M / 1Y / 3Y / 5Y windows)
CBOE_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
CBOE = ["COR1M", "COR3M", "COR10D", "COR90D", "VIXEQ", "VIX", "VIX9D", "VIX3M", "VVIX", "SPX", "SKEW", "VXN", "DSPX"]
FMP_SYM = {"SPX": "^GSPC"}    # every other index = "^" + name
NEED = ["VIX", "VIXEQ", "COR1M", "COR3M", "COR90D", "COR10D", "VIX9D", "VIX3M", "VVIX", "SPX"]


# ---------------------------------------------------------------- fetch (vendor edge; degrade, never die)
def env_val(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    try:
        m = re.search(rf'^{name}\s*=\s*"?([^"\n]+?)"?\s*$', (REPO / ".env").read_text(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def http_get(url: str, headers: dict | None = None, tries: int = 3) -> bytes:
    err = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            return urllib.request.urlopen(req, timeout=60).read()
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(f"GET failed {url[:90]}: {err}")


def parse_cboe_csv(text: str) -> dict:
    """{'YYYY-MM-DD': close}; the close is the LAST column (5-column and 2-column files)."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",") if p.strip() != ""]
        if len(parts) < 2:
            continue
        try:
            m, d, y = parts[0].split("/")
            v = float(parts[-1])
        except ValueError:
            continue
        if v > 0:
            out[f"{int(y):04d}-{int(m):02d}-{int(d):02d}"] = v
    return out


def cboe_hist(name: str) -> dict:
    try:
        return parse_cboe_csv(http_get(CBOE_CSV.format(name=name)).decode("utf-8", "ignore"))
    except Exception as e:  # noqa: BLE001
        print(f"cboe {name}: {e}", file=sys.stderr)
        return {}


def fmp_hist(sym: str, frm: str = START) -> dict:
    base = env_val("CVFORGE_BASE_URL") or "https://tap.convexvalue.com/api/data"
    url = (f"{base.rstrip('/')}/fmp/stable/historical-price-eod/light?" +
           urllib.parse.urlencode({"symbol": sym, "from": frm, "to": date.today().isoformat()}))
    try:
        rows = json.loads(http_get(url, {"Authorization": "Bearer " + env_val("CVFORGE_API_KEY")}))
    except Exception as e:  # noqa: BLE001
        print(f"fmp {sym}: {e}", file=sys.stderr)
        return {}
    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    return {str(r["date"])[:10]: float(r["price"]) for r in rows or []
            if isinstance(r, dict) and r.get("price") not in (None, 0)}


def fred_hist(series: str) -> dict:
    key = env_val("FRED_API_KEY")
    if not key:
        return {}
    url = ("https://api.stlouisfed.org/fred/series/observations?" +
           urllib.parse.urlencode({"series_id": series, "api_key": key, "file_type": "json",
                                   "observation_start": START}))
    try:
        j = json.loads(http_get(url))
    except Exception as e:  # noqa: BLE001
        print(f"fred {series}: {e}", file=sys.stderr)
        return {}
    return {o["date"]: float(o["value"]) for o in j.get("observations", []) if o["value"] not in (".", "")}


def load_panel() -> tuple[dict, dict]:
    """Every input series as {date: close} + where each came from. Cboe first, FMP fills gaps / a stale file."""
    S, src = {}, {}
    recent = (date.today() - timedelta(days=14)).isoformat()
    for name in CBOE:
        c = cboe_hist(name)
        sym = FMP_SYM.get(name, "^" + name)
        # Whole history from FMP only when the Cboe file is missing or stale; the last two weeks from FMP always,
        # so a run before Cboe posts today's row still has today (FMP closes matched Cboe's exactly, 2026-11-22).
        f = fmp_hist(sym) if not c or max(c) < (date.today() - timedelta(days=5)).isoformat() else fmp_hist(sym, recent)
        S[name] = {**f, **c}                      # Cboe wins on any date both have
        added = [d for d in f if d not in c]
        src[name] = ("cboe" if not added else "cboe+fmp") if c else ("fmp" if f else "missing")
    S["MOVE"] = fmp_hist("^MOVE")
    src["MOVE"] = "fmp" if S["MOVE"] else "missing"
    S["HY"] = fred_hist("BAMLH0A0HYM2")
    src["HY"] = "fred" if S["HY"] else "missing"
    return S, src


# ---------------------------------------------------------------- the score (pure: lists in, dicts out)
POINTS = [
    # id, name, measure label, side, level, meaning
    ("F1", "Correlation very low", "COR1M", "<=", 12,
     "Stocks barely move together, so little is left to cancel the next shock"),
    ("F2", "Stocks wild, index calm", "VIXEQ / VIX", ">=", 2.5, "Single-stock vol is hiding under a calm index"),
    ("F3", "Stock vol rising under a flat VIX", "VIXEQ 20d chg − VIX 20d chg", ">=", 2,
     "Stocks getting jumpier while the index does not show it"),
    ("F4", "Credit leaking", "HY spread 20d chg (pts)", ">=", 0.15, "Bond market pricing more risk first"),
    ("F5", "Rate vol rising, VIX calm", "MOVE 20d chg (VIX under 20)", ">=", 10,
     "Rates market getting unstable while equity vol is still calm"),
    ("F6", "Crash insurance being bought", "VVIX / VIX", ">=", 5.5, "VIX calls bid while VIX is low"),
    ("F7", "Upside correlation gone", "COR90D / COR10D", ">=", 5,
     "Puts priced as if stocks crash together, calls as if they never rally together"),
    ("F8", "VIX over-reacting to SPX", "max VIX beta on a down day, 10 sessions (VIX under 22)", ">=", 15,
     "VIX jumping far more than the SPX drop explains (normal is about 7)"),
    ("T1", "Index catching stock vol", "VIXEQ / VIX", "<=", 2.0, "Correlation bid: stress has reached the index"),
    ("T2", "Front correlation inverted", "COR1M − COR3M", ">=", 0, "Near-term correlation priced above 3-month"),
    ("T3", "Front panic", "VIX9D − VIX", ">=", 0, "9-day vol above 30-day"),
    ("T4", "VIX curve inverted", "VIX − VIX3M", ">=", 0, "30-day vol above 3-month"),
    ("T5", "Correlation jump", "COR1M 5d chg", ">=", 8, "Stocks suddenly moving as one"),
    ("T6", "VIX itself unstable", "VIX 10d vol ÷ prior 60d", ">=", 1.5, "VIX swinging far more than usual"),
    ("T7", "Upside correlation bid", "COR10D 10d chg", ">=", 5,
     "Even call-side correlation rising: everything moving together"),
    ("R1", "Correlation rolling over", "COR1M ÷ its 10d peak", "<=", 0.75, "Correlation off its peak"),
    ("R2", "Front correlation back under 3M", "COR1M − COR3M", "<=", 0, "Near-term correlation premium gone"),
    ("R3", "Ratio bouncing", "VIXEQ/VIX above its 10d low", ">=", 0.25, "Stock moves starting to cancel again"),
    ("R4", "Front panic fading", "VIX9D − VIX", "<=", 0, "9-day vol back under 30-day"),
    ("R5", "Vol-of-vol not confirming", "VVIX / VIX", "<=", 4.0, "Fear of fear lagging the VIX level"),
    ("R6", "Credit stops widening", "HY spread 5d chg (pts)", "<=", 0, "Bond market calming"),
    ("R7", "Upside correlation fading", "COR10D ÷ its 10d peak", "<=", 0.7, "Call-side correlation unwinding"),
]
GROUPS = {"F": "Fragility", "T": "Transmission", "R": "Release"}
VIX_CAP = {"F5": 20, "F8": 22}   # these two count only while equity vol is still calm


def align(S: dict) -> tuple[list, dict]:
    """Dates where every NEED series has a close (from START), MOVE / HY / SKEW / VXN forward-filled."""
    dates = sorted(d for d in set.intersection(*[set(S[k]) for k in NEED]) if d >= START)
    X = {k: [S[k][d] for d in dates] for k in NEED}
    for k in ("MOVE", "HY", "SKEW", "VXN", "DSPX"):
        src, out = S.get(k) or {}, []
        first = min(src) if src else None
        before = [d for d in src if dates and d < dates[0]]
        last = src[max(before)] if before else None   # the value in force on the first day
        for d in dates:
            if d in src:
                last = src[d]
            out.append(last if first and d >= first else None)
        X[k] = out
    return dates, X


def compute(dates: list, X: dict) -> dict:
    n = len(dates)
    vix, spx = X["VIX"], X["SPX"]
    ratio = [X["VIXEQ"][i] / vix[i] for i in range(n)]
    pct = lambda a, i: (a[i] / a[i - 1] - 1) * 100 if i > 0 and a[i - 1] else None  # noqa: E731

    def chg(a, i, lag):
        return None if i - lag < 0 or a[i] is None or a[i - lag] is None else a[i] - a[i - lag]

    def win(a, i, w, f):
        xs = [a[j] for j in range(max(0, i - w + 1), i + 1) if a[j] is not None]
        return f(xs) if xs else None

    beta_day, vvix_beta_day = [None] * n, [None] * n
    for i in range(1, n):
        s = pct(spx, i)
        if s is not None and s <= -0.3:
            beta_day[i] = -pct(vix, i) / s
            vvix_beta_day[i] = -pct(X["VVIX"], i) / s
    lr = [None] + [math.log(vix[i] / vix[i - 1]) for i in range(1, n)]

    def beta21(i, w=21):
        if i < w + 1:
            return None
        a = [pct(spx, j) for j in range(i - w + 1, i + 1)]
        c = [pct(vix, j) for j in range(i - w + 1, i + 1)]
        ma, mc = st.mean(a), st.mean(c)
        v = sum((x - ma) ** 2 for x in a)
        return -sum((x - ma) * (y - mc) for x, y in zip(a, c)) / v if v else None

    def metric(pid, i):
        if pid == "F1":
            return X["COR1M"][i]
        if pid in ("F2", "T1"):
            return ratio[i]
        if pid == "F3":
            a, b = chg(X["VIXEQ"], i, 20), chg(vix, i, 20)
            return None if a is None or b is None else a - b
        if pid == "F4":
            return chg(X["HY"], i, 20)
        if pid == "F5":
            return chg(X["MOVE"], i, 20)
        if pid in ("F6", "R5"):
            return X["VVIX"][i] / vix[i]
        if pid == "F7":
            return X["COR90D"][i] / max(X["COR10D"][i], 0.5)
        if pid == "F8":
            return win(beta_day, i, 10, max) or 0.0
        if pid in ("T2", "R2"):
            return X["COR1M"][i] - X["COR3M"][i]
        if pid in ("T3", "R4"):
            return X["VIX9D"][i] - vix[i]
        if pid == "T4":
            return vix[i] - X["VIX3M"][i]
        if pid == "T5":
            return chg(X["COR1M"], i, 5)
        if pid == "T6":
            if i < 70:
                return None
            a = lr[i - 69: i + 1]
            d = st.pstdev(a[:-10])
            return st.pstdev(a[-10:]) / d if d else None
        if pid == "T7":
            return chg(X["COR10D"], i, 10)
        if pid == "R1":
            pk = win(X["COR1M"], i, 10, max)
            return X["COR1M"][i] / pk if pk else None
        if pid == "R3":
            return ratio[i] - win(ratio, i, 10, min)
        if pid == "R6":
            return chg(X["HY"], i, 5)
        if pid == "R7":
            pk = win(X["COR10D"], i, 10, max)
            return X["COR10D"][i] / pk if pk else None
        raise KeyError(pid)

    M, ON = {}, {}
    for pid, _n, _m, side, lvl, _mean in POINTS:
        M[pid] = [metric(pid, i) for i in range(n)]
        cap = VIX_CAP.get(pid)
        ON[pid] = [None if v is None else ((v >= lvl if side == ">=" else v <= lvl) and (cap is None or vix[i] < cap))
                   for i, v in enumerate(M[pid])]

    score = {g: [sum(1 for pid, *_ in POINTS if pid[0] == g and ON[pid][i]) for i in range(n)] for g in GROUPS}
    phase = []
    for i in range(n):
        stress = any(score["T"][j] >= 3 for j in range(max(0, i - 10), i))
        if score["T"][i] >= 3:
            phase.append("RELEASING" if score["R"][i] >= 4 else "TRANSMITTING")
        elif stress and score["R"][i] >= 4:
            phase.append("RELEASING")
        elif score["F"][i] >= 3:
            phase.append("LOADED")
        else:
            phase.append("QUIET")

    # ---- what followed (2014 -> now; overlapping windows) ----
    def fwd_max_vix(i):
        f = vix[i + 1: i + 21]
        return (max(f) / vix[i] - 1) * 100 if len(f) == 20 else None

    def spx_dd(i):
        f = spx[i + 1: i + 21]
        return (min(f) / spx[i] - 1) * 100 if len(f) == 20 else None

    def new_high(i):
        f = vix[i + 1: i + 21]
        return None if len(f) < 20 else (100.0 if max(f) > max(vix[max(0, i - 10): i + 1]) else 0.0)

    in_stress = [any(score["T"][j] >= 3 for j in range(max(0, i - 10), i + 1)) for i in range(n)]
    OUT = {"F": (fwd_max_vix, "biggest VIX rise in the next 20 days, %", False),
           "T": (spx_dd, "worst SPX drop in the next 20 days, %", False),
           "R": (new_high, "chance VIX makes a new high within 20 days (during stress), %", True)}

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return (round(st.mean(xs), 1), len(xs)) if xs else (None, 0)

    def q(vals, f):
        vals = sorted(v for v in vals if v is not None)
        return vals[min(len(vals) - 1, int(f * len(vals)))] if vals else None

    last, ago = n - 1, max(0, n - 6)
    points = []
    for pid, name, measure, side, lvl, meaning in POINTS:
        fn, what, only_stress = OUT[pid[0]]
        idx = [i for i in range(n) if in_stress[i]] if only_stress else range(n)
        on = avg([fn(i) for i in idx if ON[pid][i]])
        off = avg([fn(i) for i in idx if ON[pid][i] is False])
        points.append({"id": pid, "group": GROUPS[pid[0]], "name": name, "measure": measure, "side": side,
                       "level": lvl, "cap": VIX_CAP.get(pid), "meaning": meaning,
                       "now": rnd(M[pid][last], 3), "ago5": rnd(M[pid][ago], 3),
                       "on": bool(ON[pid][last]), "on5": bool(ON[pid][ago]),
                       "lo": rnd(q(M[pid], 0.05), 3), "hi": rnd(q(M[pid], 0.95), 3),
                       "ev_what": what, "ev_on": on[0], "ev_off": off[0], "ev_days": on[1]})
    tests = []
    for g, (fn, what, only_stress) in OUT.items():
        idx = [i for i in range(n) if in_stress[i]] if only_stress else range(n)
        for k in range(0, 9):
            a = avg([fn(i) for i in idx if score[g][i] == k])
            if a[1] >= 5:
                tests.append({"group": GROUPS[g], "on": k, "what": what, "avg": a[0], "days": a[1]})
    ph_tests = [{"phase": p, "vix_rise_20d": avg([fwd_max_vix(i) for i in range(n) if phase[i] == p])[0],
                 "spx_drop_20d": avg([spx_dd(i) for i in range(n) if phase[i] == p])[0],
                 "days": phase.count(p)} for p in ("QUIET", "LOADED", "TRANSMITTING", "RELEASING")]

    # ---- daily series for the charts (last SERIES_DAYS rows) ----
    lo = max(0, n - SERIES_DAYS)
    ud = []
    for i in range(n):
        w = [lr2 for lr2 in ((math.log(spx[j] / spx[j - 1])) for j in range(max(1, i - 62), i + 1))]
        up = [x for x in w if x > 0]
        dn = [x for x in w if x < 0]
        ud.append(math.sqrt(sum(x * x for x in dn) / len(dn)) / math.sqrt(sum(x * x for x in up) / len(up))
                  if len(up) > 5 and len(dn) > 5 else None)
    series = {"date": dates[lo:], "vix": [rnd(v, 2) for v in vix[lo:]], "vixeq": [rnd(v, 2) for v in X["VIXEQ"][lo:]],
              "ratio": [rnd(v, 3) for v in ratio[lo:]], "implied_corr": [rnd(100 / r ** 2, 1) for r in ratio[lo:]],
              "cor1m": X["COR1M"][lo:], "cor3m": X["COR3M"][lo:], "cor10d": X["COR10D"][lo:],
              "cor90d": X["COR90D"][lo:], "skew": X["SKEW"][lo:], "updown": [rnd(v, 3) for v in ud[lo:]],
              "beta21": [rnd(beta21(i), 2) for i in range(lo, n)], "beta_day": [rnd(v, 1) for v in beta_day[lo:]],
              "vvix_beta_day": [rnd(v, 1) for v in vvix_beta_day[lo:]],
              "F": score["F"][lo:], "T": score["T"][lo:], "R": score["R"][lo:], "phase": phase[lo:]}
    for pid, *_ in POINTS:
        series[pid] = [rnd(v, 3) for v in M[pid][lo:]]
        series[pid + "_on"] = [1 if o else 0 for o in ON[pid][lo:]]

    # ---- correlation spikes: the biggest COR1M peaks, each with the calm low before it ----
    events = []
    c1 = X["COR1M"]
    i = 0
    while i < n:
        if c1[i] >= 27 and c1[i] == max(c1[max(0, i - 30): min(n, i + 31)]):
            pre = range(max(0, i - 120), i)
            j = min(pre, key=lambda k: c1[k]) if pre else i
            events.append({"low_date": dates[j], "cor1m_low": c1[j], "peak_date": dates[i], "cor1m_peak": c1[i],
                           "cor10d_peak": X["COR10D"][i], "cor90d_peak": X["COR90D"][i], "vix_peak": vix[i]})
            i += 30
        i += 1

    return {"asof": dates[-1], "n_days": n, "start": dates[0], "phase": phase[last], "phase5": phase[ago],
            "score": {g: score[g][last] for g in GROUPS}, "score5": {g: score[g][ago] for g in GROUPS},
            "count": {g: sum(1 for p in POINTS if p[0][0] == g) for g in GROUPS},
            "now": {"vix": vix[last], "vixeq": X["VIXEQ"][last], "ratio": rnd(ratio[last], 2),
                    "implied_corr": rnd(100 / ratio[last] ** 2, 1), "cor1m": X["COR1M"][last],
                    "cor3m": X["COR3M"][last], "cor10d": X["COR10D"][last], "cor90d": X["COR90D"][last],
                    "beta21": rnd(beta21(last), 1), "vvix": X["VVIX"][last]},
            "points": points, "tests": tests, "phase_tests": ph_tests, "events": events[-8:], "series": series}


def rnd(v, k):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, k)


def updown_years(spx: dict) -> list:
    """SPX realized vol on up days vs down days, by year (from 1987)."""
    ds = sorted(spx)
    yr = {}
    for a, b in zip(ds, ds[1:]):
        if b[:4] >= "1987":
            yr.setdefault(b[:4], []).append(math.log(spx[b] / spx[a]))
    out = []
    for y, xs in sorted(yr.items()):
        up, dn = [x for x in xs if x > 0], [x for x in xs if x < 0]
        if len(up) < 5 or len(dn) < 5:
            continue
        v = lambda a: math.sqrt(sum(t * t for t in a) / len(a)) * math.sqrt(252) * 100  # noqa: E731
        out.append({"year": y, "up_days": len(up), "down_days": len(dn), "up_vol": round(v(up), 1),
                    "down_vol": round(v(dn), 1), "ratio": round(v(dn) / v(up), 2), "total_vol": round(v(xs), 1)})
    return out


def run(out: Path | None = None, page: bool = True) -> dict:
    S, src = load_panel()
    missing = [k for k in NEED if not S.get(k)]
    if missing:
        raise SystemExit(f"fuse_tab: no data for {missing} (sources {src})")
    dates, X = align(S)
    o = compute(dates, X)
    o["years"] = updown_years(S["SPX"])
    try:  # 2026-11-22: "is this fear broad or narrow?" (scripts/fear_width.py) for the Risk page; never stops the tab
        import fear_width
        o["width"] = fear_width.build(S["VIX"], S["COR1M"], S["SPX"])
    except Exception as e:  # noqa: BLE001
        print(f"fear_width failed: {e!r}")
    try:  # 2026-11-22: Nations TailDex read (scripts/tdex_read.py) for the Risk page "fear" group; never stops the tab
        import tdex_read
        o["tdex"] = tdex_read.build(tdex_read.fetch(), S["VIX"], S["SPX"], S.get("VIX9D"), S.get("VIX3M"))
        src["TDEX"] = "yahoo" if o["tdex"] else "missing"
    except Exception as e:  # noqa: BLE001
        print(f"tdex_read failed: {e!r}")
    o["sources"] = src
    o["built"] = datetime.now().isoformat(timespec="seconds")
    out = out or DATA_DIR / "fusetab.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(o, separators=(",", ":")), encoding="utf-8")
    tmp.replace(out)
    try:  # zone cards (scripts/zone_cards.py, 2026-11-22) - same panel; a failure here never stops the Fuse tab
        import zone_cards
        zc = zone_cards.write(dates, X, out.parent, fuse=o)
        o["zones"] = {"n": len(zc["cards"]), "in_zone": sum(c["state"] == "zone" for c in zc["cards"])}
    except Exception as e:  # noqa: BLE001
        print(f"zone_cards failed: {e!r}")
        o["zones"] = {"error": repr(e)[:200]}
    try:  # 2026-11-22: merged Risk page (risk_page.py) - rebuilt here too so it never waits for risk_tab
        import risk_page
        risk_page.write(out.parent)
    except Exception as e:  # noqa: BLE001
        print(f"risk_page failed: {e!r}")
    if page and PAGE_SRC.exists():
        shutil.copyfile(PAGE_SRC, out.parent / "fuse.html")
    return o


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-page", action="store_true")
    a = ap.parse_args()
    o = run(a.out, page=not a.no_page)
    sc = o["score"]
    print(f"fusetab.json asof {o['asof']}: {o['phase']}  F {sc['F']}/8  T {sc['T']}/7  R {sc['R']}/7  "
          f"ratio {o['now']['ratio']}  zones {o.get('zones')}  sources {o['sources']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
