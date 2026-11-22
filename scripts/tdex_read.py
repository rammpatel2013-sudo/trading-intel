"""tdex_read.py - created 2026-11-22. Nations TailDex (TDEX) read for the Risk page ("fear" group).

TDEX = cost of 30-day 3-sigma SPY puts. On its own it mixes two things: the VIX level and extra demand for tail puts.
    t_v       = TDEX / VIX. Median ~0.78 since 2013. >= 1.1 = tail puts bid beyond what the VIX level implies.
    read      = Pre-hedge  (TDEX close >= 25, SPX within 5% of its 1-year high, VIX < 31): puts bought BEFORE the drop.
                           2021-09, 2021-12, 2024-12, 2026-03 -> more downside first (-3.5 to -7.4% in 3 months).
                Flush      (TDEX >= 25, VIX >= 35, SPX 8%+ off high, VIX9D - VIX3M >= 6): capitulation profile.
                           2018-02, 2020-03, 2024-08, 2025-04 -> low within days. 2022-04 (term +2.9) was not.
                Elevated   (TDEX >= 25, neither) / Normal.
    episodes  = weekly TDEX close >= 25 runs (6-week gap), each with what SPX did next.
Data: Yahoo ^TDEX daily (2013-06 on). Closes only: the intraday highs are full of opening misprints
(2017-02-21 high 237 / close 9.7; 2019-07-19 80 / 13.9; 2025-11-20 open=high 35.6 / close 24.4), so any
"weekly high > 35" rule counts junk. Test + episode table: project doc claude/tdex-tail-index-test-20261122.md.
Descriptor only (CLAUDE.md rule 4) - not in the Fuse score until it passes the Caltropia gauntlet.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETDEX?period1={p1}&period2={p2}&interval=1d"
HI, PRE_DD, PRE_VIX, FL_VIX, FL_DD, FL_TERM, TV_BID = 25.0, -5.0, 31.0, 35.0, -8.0, 6.0, 1.1


def fetch() -> dict:
    """{date: [open, high, low, close]} from Yahoo; {} on any failure (never stops the caller)."""
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    url = YAHOO.format(p1=1370000000, p2=p2)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trading-intel tdex_read)"})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read())["chart"]["result"][0]
    except Exception as e:  # noqa: BLE001
        print(f"tdex fetch: {e!r}", file=sys.stderr)
        return {}
    q = r["indicators"]["quote"][0]
    out = {}
    for i, ts in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        d = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        out[d] = [q["open"][i], q["high"][i], q["low"][i], float(c)]
    return out


def _pct(xs: list, v: float) -> int:
    xs = sorted(xs)
    return round(100.0 * bisect_left(xs, v) / len(xs)) if xs else 50


def classify(tdex: float, vix: float, dd: float, term) -> str:
    if tdex < HI:
        return "Normal"
    if dd > PRE_DD and vix < PRE_VIX:
        return "Pre-hedge"
    if vix >= FL_VIX and dd <= FL_DD and term is not None and term >= FL_TERM:
        return "Flush"
    return "Elevated"


def _week(d: str) -> str:
    y, m, dd = map(int, d.split("-"))
    x = date(y, m, dd)
    return (x + timedelta(days=(4 - x.weekday()) % 7)).isoformat()   # the Friday of that week


def build(tdx: dict, vix: dict, spx: dict, v9: dict | None = None, v3: dict | None = None) -> dict:
    v9, v3 = v9 or {}, v3 or {}
    dates = sorted(set(tdx) & set(vix) & set(spx))
    if len(dates) < 300:
        return {}
    T = [tdx[d][3] for d in dates]
    V = [vix[d] for d in dates]
    P = [spx[d] for d in dates]
    n = len(dates)
    DD, hi = [], []
    for i in range(n):
        hi.append(P[i])
        if len(hi) > 252:
            hi.pop(0)
        DD.append(100 * (P[i] / max(hi) - 1))
    term = [None if d not in v9 or d not in v3 else v9[d] - v3[d] for d in dates]
    TV = [T[i] / V[i] for i in range(n)]
    t = n - 1
    read = classify(T[t], V[t], DD[t], term[t])

    # weekly-close >= 25 episodes; peak = highest TDEX close in the run
    wk: dict = {}
    for i, d in enumerate(dates):
        wk.setdefault(_week(d), []).append(i)
    runs, last = [], None
    for w in sorted(wk):
        if max(T[i] for i in wk[w]) < HI:
            continue
        if runs and last and (datetime.fromisoformat(w) - datetime.fromisoformat(last)).days <= 42:
            runs[-1] += wk[w]
        else:
            runs.append(list(wk[w]))
        last = w
    eps = []
    for r in runs:
        i = max(r, key=lambda k: T[k])
        f3 = P[i:i + 64]
        f6 = P[i:i + 127]
        lo = i + f6.index(min(f6))
        full = i + 252 < n
        eps.append({"date": dates[i], "tdex": round(T[i], 1), "vix": round(V[i], 1), "tv": round(TV[i], 2),
                    "dd": round(DD[i], 1), "term": None if term[i] is None else round(term[i], 1),
                    "read": classify(T[i], V[i], DD[i], term[i]),
                    "more3m": round(100 * (min(f3) / P[i] - 1), 1), "days_low": (datetime.fromisoformat(dates[lo]) - datetime.fromisoformat(dates[i])).days,
                    "max12m": round(100 * (max(P[i:i + 253]) / P[i] - 1), 1), "done": full})
    k0 = max(0, n - 252)
    return {"asof": dates[t], "tdex": round(T[t], 2), "vix": round(V[t], 2), "tv": round(TV[t], 2),
            "dd": round(DD[t], 1), "term": None if term[t] is None else round(term[t], 1), "read": read,
            "pct": _pct(T, T[t]), "pct_tv": _pct(TV, TV[t]), "tv_med": round(sorted(TV)[n // 2], 2),
            "start": dates[0], "dates": dates[k0:], "s_tdex": [round(x, 2) for x in T[k0:]],
            "s_tv": [round(x, 3) for x in TV[k0:]], "episodes": eps}
