#!/usr/bin/env python3
"""alt_score.py - created 2026-11-05.

Scores five alternative calculations against what the cockpit uses today, on every stored
session (Convex raw + minute flow files on the NAS). Plain-English HTML + JSON out.

  T1 Flip     current flip_point.py maths (0DTE T floored at 1 h, r=0.04) vs fixed (true minutes
              to 16:00, r=0). Score: distance from the flip to the close / to the day's path.
  T2 Smile    how fixed-strike IV really moves when SPX moves, 15 min ahead:
              plain (sticky strike, IV unchanged) vs current Smile button (sticky moneyness,
              smile slides with spot) vs measured (per-moneyness drift + beta, fitted only on
              earlier data). Score: error of the predicted IV change (vol points).
  T3 3:30     dealer delta on the expiring book at 15:30 / 15:45 per book (naive OI, OI + vol,
              Ledger) vs the SPX move to the close (Garcia-Ares et al.: high dealer delta -> up).
  T4 Gate     which gamma goes with a calmer next 30 minutes: 0DTE vs longer expiries vs total.
  T5 Cone     close / path inside the AM band: IV cone vs Gap strip (+-1 straddle) vs
              1.2533 x straddle (= 1 sigma).

No look-ahead: books use 09:31 OI (or the morning ledger), IVs/deltas of that minute, and the
measured smile beta is fitted only on pairs that finished before the minute being predicted.

Usage (NAS):  bash scripts/nas/run_script.sh alt_score            # every stored day
              bash scripts/nas/run_script.sh alt_score --days 20261103,20261104
Descriptive research only (rule 4) - nothing here writes signals.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # .../scripts
CVX = HERE / "cvxmap"
YEAR_MIN = 525600.0                               # calendar minutes / year (map clock)
CLOSE_MIN = 960                                   # 16:00
SQ2PI = math.sqrt(2 * math.pi)
H_SMILE = 15                                      # T2 horizon, minutes


# ------------------------------------------------------------------ small helpers
def mm(t):
    return int(t[:2]) * 60 + int(t[3:5])


def hh(m):
    return "%02d:%02d" % (m // 60, m % 60)


def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def npdf(x):
    return math.exp(-0.5 * x * x) / SQ2PI


def bs_d1(S, K, sig, T, r=0.0):
    return (math.log(S / K) + (r + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))


def bs_gamma(S, K, sig, T, r=0.0):
    if sig <= 0 or T <= 0:
        return 0.0
    return npdf(bs_d1(S, K, sig, T, r)) / (S * sig * math.sqrt(T))


def bs_delta(S, K, sig, T, kind):
    if sig <= 0 or T <= 0:
        intr = (S > K) if kind == "C" else (S < K)
        return (1.0 if intr else 0.0) * (1 if kind == "C" else -1)
    n = ncdf(bs_d1(S, K, sig, T))
    return n if kind == "C" else n - 1.0


def t_years(minute):
    return max(CLOSE_MIN - minute, 0.5) / YEAR_MIN


def rank(xs):
    o = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(o):
        j = i
        while j + 1 < len(o) and xs[o[j + 1]] == xs[o[i]]:
            j += 1
        for k in range(i, j + 1):
            r[o[k]] = (i + j) / 2.0
        i = j + 1
    return r


def corr(a, b):
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if sa == 0 or sb == 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def spearman(a, b):
    return corr(rank(a), rank(b))


# ------------------------------------------------------------------ inputs
def load_flow_spx(path, day_iso):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if '"SPX"' not in line[:80]:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("s") != "SPX" or j.get("d") != day_iso:
                continue
            t = j.get("t", "")
            if "09:30" <= t <= "16:00":
                out[t] = j
    return out


def otm_iv(j, i, S):
    """OTM-side IV of strike index i in minute j (falls back to the other side)."""
    k = j["k"][i]
    a, b = (j["ci"][i], j["pi"][i]) if k >= S else (j["pi"][i], j["ci"][i])
    for v in (a, b):
        if v and 0.01 < v < 3.0:
            return v
    return None


def smile_of(j):
    """[(K, iv)] sorted, OTM side, at this minute's spot."""
    S = j["x"]
    pts = []
    for i, k in enumerate(j["k"]):
        v = otm_iv(j, i, S)
        if v:
            pts.append((float(k), v))
    pts.sort()
    return pts


def iv_at(pts, K):
    """Linear interpolation, flat beyond the edges."""
    if not pts:
        return None
    if K <= pts[0][0]:
        return pts[0][1]
    if K >= pts[-1][0]:
        return pts[-1][1]
    lo, hi = 0, len(pts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if pts[mid][0] <= K:
            lo = mid
        else:
            hi = mid
    (k0, v0), (k1, v1) = pts[lo], pts[hi]
    return v0 + (v1 - v0) * (K - k0) / (k1 - k0)


def first_at_or_after(flow, t0):
    for t in sorted(flow):
        if t >= t0:
            return t
    return None


def get_extract(day, data, out_root):
    ymd = day.strftime("%Y%m%d")
    p = out_root / ymd / "extract.json"
    if p.is_file():
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    raw = data / ("raw_%s.ndjson.gz" % ymd)
    if not raw.is_file():
        return None
    sys.path.insert(0, str(HERE))
    import cvx_replay  # the nightly replay's own extractor (same OI / week book)
    ex = cvx_replay.extract(raw, day)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(ex, fh, separators=(",", ":"))
    return ex


def oi_book(ex):
    o = ((ex.get("oi") or {}).get("SPX") or {}).get("oi") or {}
    return {float(k): (float(v[0] or 0), float(v[1] or 0)) for k, v in o.items()}


def load_ledger(data, ymd, day_iso):
    p = data / ("ledger_%s.json" % ymd)
    if not p.is_file():
        return None
    try:
        j = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    if str(j.get("as_of", ""))[:10] != day_iso:
        return None
    book = {}
    for r in j.get("rows") or []:
        if str(r.get("exp", ""))[:10] != day_iso:
            continue
        k = float(r["k"])
        c, p_ = book.get(k, (0.0, 0.0))
        dealer = -float(r.get("raw") or 0)          # ledger = customers' running position
        if str(r.get("r", "")).upper().startswith("C"):
            c += dealer
        else:
            p_ += dealer
        book[k] = (c, p_)
    return book or None


# ------------------------------------------------------------------ T1 flip
def net_gex(S, book, pts, T, r):
    g = 0.0
    for k, (c_oi, p_oi) in book.items():
        if abs(k / S - 1) > 0.05:
            continue
        v = iv_at(pts, k)
        if not v:
            continue
        gam = bs_gamma(S, k, v, T, r) * 100 * S * S * 0.01
        g += gam * (c_oi - p_oi)                     # flip_point.py sign: calls +, puts -
    return g


def flip_root(S0, book, pts, T, r):
    best = None
    prev_s, prev_g = None, None
    for i in range(-300, 301):
        s = S0 * (1 + i * 0.0001)
        g = net_gex(s, book, pts, T, r)
        if prev_g is not None and (g == 0 or (g > 0) != (prev_g > 0)):
            lo, hi, glo = prev_s, s, prev_g
            for _ in range(30):
                m = 0.5 * (lo + hi)
                gm = net_gex(m, book, pts, T, r)
                if (gm > 0) == (glo > 0):
                    lo, glo = m, gm
                else:
                    hi = m
            x = 0.5 * (lo + hi)
            if best is None or abs(x - S0) < abs(best - S0):
                best = x
        prev_s, prev_g = s, g
    return best


def t1_flip(flow, book, path, close):
    rows = []
    for t0 in ("09:31", "12:00", "14:00"):
        t = first_at_or_after(flow, t0)
        if not t:
            continue
        j = flow[t]
        S, pts, m = j["x"], smile_of(j), mm(t)
        cur = flip_root(S, book, pts, 1.0 / (365.0 * 24.0), 0.04)
        fix = flip_root(S, book, pts, t_years(m), 0.0)
        rest = [x for tt, x in path if tt >= t]

        def sc(f):
            if f is None:
                return None
            return {"flip": round(f, 1), "vs_spot": round(f - S, 1), "to_close": round(abs(f - close), 1),
                    "touched": bool(rest and min(rest) <= f <= max(rest)),
                    "closest": round(min(abs(x - f) for x in rest), 1) if rest else None}
        rows.append({"t": t, "spot": round(S, 1), "current": sc(cur), "fixed": sc(fix)})
    return rows


# ------------------------------------------------------------------ T2 smile
def bucket(lm):
    if lm < -0.005:
        return "put wing"
    if lm < 0:
        return "near put"
    if lm < 0.005:
        return "near call"
    return "call wing"


def t2_pairs(flow):
    """One record per (minute, strike): IV change over H_SMILE min, spot change, both predictions."""
    times = sorted(flow)
    recs = []
    for t in times:
        m = mm(t)
        if m < mm("09:45") or m + H_SMILE > mm("15:45") or (m % 5):
            continue
        t2 = hh(m + H_SMILE)
        if t2 not in flow:
            continue
        a, b = flow[t], flow[t2]
        S1, S2 = a["x"], b["x"]
        pa, pb = smile_of(a), smile_of(b)
        for K, v1 in pa:
            lm = math.log(K / S1)
            if abs(lm) > 0.01:
                continue
            v2 = iv_at(pb, K) if pb and pb[0][0] <= K <= pb[-1][0] else None
            if not v2:
                continue
            sm = iv_at(pa, K * S1 / S2)                  # sticky moneyness: same K/S as before
            recs.append({"m": m, "b": bucket(lm), "dS": (S2 - S1) / S1, "dv": v2 - v1,
                         "smile": (sm - v1) if sm else 0.0})
    return recs


def t2_score(days_recs):
    """Walk forward through all days; measured model = per-bucket a + beta*dS fitted only on
    pairs that ended before the current minute (earlier days + earlier same day)."""
    stats = {}                                           # bucket -> sums
    err = {"plain": [], "smile": [], "drift": [], "measured": []}
    per_day = []
    for day, recs in days_recs:
        e_day = {k: [] for k in err}
        recs = sorted(recs, key=lambda r: r["m"])
        pending = []
        for r in recs:
            # release pairs that have finished (end time <= this start)
            keep = []
            for q in pending:
                if q["m"] + H_SMILE <= r["m"]:
                    s = stats.setdefault(q["b"], [0, 0.0, 0.0, 0.0, 0.0])
                    s[0] += 1; s[1] += q["dS"]; s[2] += q["dv"]; s[3] += q["dS"] * q["dS"]; s[4] += q["dS"] * q["dv"]
                else:
                    keep.append(q)
            pending = keep
            s = stats.get(r["b"])
            if s and s[0] >= 40:
                n, sx, sy, sxx, sxy = s
                den = n * sxx - sx * sx
                beta = (n * sxy - sx * sy) / den if den > 0 else 0.0
                a = (sy - beta * sx) / n
                pred_m, pred_d = a + beta * r["dS"], sy / n
                for name, pred in (("plain", 0.0), ("smile", r["smile"]), ("drift", pred_d), ("measured", pred_m)):
                    e = (r["dv"] - pred) * 100                # vol points
                    err[name].append(e)
                    e_day[name].append(e)
            pending.append(r)
        for q in pending:                                    # day over: everything finished
            s = stats.setdefault(q["b"], [0, 0.0, 0.0, 0.0, 0.0])
            s[0] += 1; s[1] += q["dS"]; s[2] += q["dv"]; s[3] += q["dS"] * q["dS"]; s[4] += q["dS"] * q["dv"]
        per_day.append({"day": day, "n": len(e_day["plain"]),
                        **{k: (round(math.sqrt(sum(x * x for x in v) / len(v)), 3) if v else None) for k, v in e_day.items()}})
    betas = {}
    for b, (n, sx, sy, sxx, sxy) in stats.items():
        den = n * sxx - sx * sx
        if den > 0:
            beta = (n * sxy - sx * sy) / den
            betas[b] = {"n": n, "vol_pts_per_1pct": round(beta * 100 * 0.01, 2)}
    tot = {k: (round(math.sqrt(sum(x * x for x in v) / len(v)), 3) if v else None) for k, v in err.items()}
    return {"rmse": tot, "n": len(err["plain"]), "per_day": per_day, "beta": betas}


# ------------------------------------------------------------------ T3 3:30 expiring delta
def dealer_delta(book, j, T):
    """Dealer option delta in $. Uses the feed's own deltas (forward-based, same as the cockpit)
    for strikes the minute log carries; BS on spot for strikes outside it (deep ITM/OTM, ~0/1)."""
    S, pts = j["x"], smile_of(j)
    feed = {float(k): (j["cd"][i], j["pd"][i]) for i, k in enumerate(j["k"])}
    D = 0.0
    for k, (qc, qp) in book.items():
        fc, fp = feed.get(k, (None, None))
        v = iv_at(pts, k) or 0.2
        dc = fc if fc is not None else bs_delta(S, k, v, T, "C")
        dp = fp if fp is not None else bs_delta(S, k, v, T, "P")
        D += qc * dc + qp * dp
    return D * 100 * S                                     # $ delta (dealer option position)


def vol_at(j):
    return {float(k): (float(j["cv"][i] or 0), float(j["pv"][i] or 0)) for i, k in enumerate(j["k"])}


def t3_books(flow, oi, ledger, t):
    j = flow[t]
    naive = {k: (c, -p) for k, (c, p) in oi.items()}           # customers long puts / short calls
    v = vol_at(j)
    oiv = {}
    for k in set(oi) | set(v):
        c, p = oi.get(k, (0.0, 0.0))
        vc, vp = v.get(k, (0.0, 0.0))
        oiv[k] = (c + vc, -(p + vp))
    books = {"naive OI": naive, "OI + vol": oiv}
    if ledger:
        books["Ledger"] = ledger
    return books


def t3_day(flow, oi, ledger, close):
    out = {}
    for t0 in ("15:30", "15:45"):
        t = first_at_or_after(flow, t0)
        if not t or mm(t) > mm(t0) + 2:
            continue
        j = flow[t]
        S, T = j["x"], t_years(mm(t))
        ret_bp = (close / S - 1) * 1e4
        out[t0] = {"spot": round(S, 2), "ret_bp": round(ret_bp, 1),
                   "D_bn": {name: round(dealer_delta(bk, j, T) / 1e9, 3) for name, bk in t3_books(flow, oi, ledger, t).items()}}
    return out


# ------------------------------------------------------------------ T4 gate
def gamma_books(flow, oi, week, t):
    j = flow[t]
    S, pts, T = j["x"], smile_of(j), t_years(mm(t))
    g0 = 0.0
    for k, (c, p) in oi.items():
        if abs(k / S - 1) > 0.05:
            continue
        v = iv_at(pts, k) or 0.2
        g0 += bs_gamma(S, k, v, T) * (c - p) * 100 * S * S * 0.01
    gw = 0.0
    for r in week:
        dte = int(r.get("dte") or 0)
        if dte <= 0:
            continue
        k = float(r["k"])
        Tw = (dte * 1440 + (CLOSE_MIN - mm(t))) / YEAR_MIN
        for oi_, iv_, sgn in ((r.get("cOI"), r.get("cIV"), 1), (r.get("pOI"), r.get("pIV"), -1)):
            if oi_ and iv_ and iv_ > 0.01:
                gw += sgn * float(oi_) * bs_gamma(S, k, float(iv_), Tw) * 100 * S * S * 0.01
    return g0, gw


def t4_day(flow, oi, week, path_d):
    rows = []
    for m in range(mm("10:00"), mm("15:30") + 1, 30):
        t = hh(m)
        if t not in flow:
            continue
        nxt = [path_d.get(hh(x)) for x in range(m, m + 31)]
        nxt = [x for x in nxt if x]
        if len(nxt) < 25:
            continue
        rv = sum(math.log(b / a) ** 2 for a, b in zip(nxt, nxt[1:])) * 1e8   # bp^2
        g0, gw = gamma_books(flow, oi, week, t)
        rows.append({"t": t, "g0": g0 / 1e9, "gw": gw / 1e9, "gt": (g0 + gw) / 1e9, "rv": rv})
    return rows


# ------------------------------------------------------------------ T5 cone
def t5_day(flow, path, close):
    t = first_at_or_after(flow, "09:31")
    j = flow[t]
    S, T = j["x"], t_years(mm(t))
    pts = smile_of(j)
    iv = iv_at(pts, S)
    i = min(range(len(j["k"])), key=lambda n: abs(j["k"][n] - S))
    K = j["k"][i]
    straddle = (j["cm"][i] or 0) + (j["pm"][i] or 0)       # nearest-to-spot strike, mids
    sig_iv = S * iv * math.sqrt(T) if iv else None
    xs = [x for tt, x in path if tt >= t]
    out = {"t": t, "spot": round(S, 1), "atm_iv": round(iv, 4) if iv else None, "straddle": round(straddle, 2),
           "close": round(close, 1), "hi": round(max(xs), 1), "lo": round(min(xs), 1)}
    for name, w in (("IV cone 1sd", sig_iv), ("Gap strip 1 straddle", straddle), ("1.2533 x straddle", 1.2533 * straddle)):
        if not w:
            continue
        out[name] = {"width": round(w, 1), "close_in_1": abs(close - S) <= w, "close_in_2": abs(close - S) <= 2 * w,
                     "path_in_1": (max(xs) - S) <= w and (S - min(xs)) <= w,
                     "touch_up": max(xs) >= S + w, "touch_dn": min(xs) <= S - w}
    return out


# ------------------------------------------------------------------ report
CSS = """body{background:#0d1117;color:#f0f3f6;font:20px/1.5 system-ui,Segoe UI,Arial,sans-serif;margin:24px;max-width:1200px}
h1{font-size:34px;margin:0 0 8px}h2{font-size:28px;margin:32px 0 8px;border-bottom:2px solid #30363d}
table{border-collapse:collapse;margin:10px 0;font-size:19px}td,th{border:1px solid #30363d;padding:6px 10px;text-align:right}
th{background:#161b22;color:#fff}td:first-child,th:first-child{text-align:left}.read{background:#161b22;border-left:6px solid #58a6ff;
padding:10px 14px;margin:10px 0}.good{color:#56d364;font-weight:700}.bad{color:#ff7b72;font-weight:700}.n{color:#f2cc60}"""


def tbl(head, rows):
    h = "".join("<th>%s</th>" % x for x in head)
    b = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % ("" if c is None else c) for c in r) for r in rows)
    return "<table><tr>%s</tr>%s</table>" % (h, b)


def html_report(R):
    p = ["<!doctype html><meta charset=utf-8><title>New calculations vs current</title><style>%s</style>" % CSS,
         "<h1>New calculations vs what the cockpit uses now</h1>",
         "<div class=read>%d sessions scored (%s). <span class=n>Small sample:</span> enough to see if a new "
         "calculation is clearly worse, not enough to prove one is better. Each new night adds a day.</div>"
         % (len(R["days"]), ", ".join(R["days"]))]

    # T1
    p.append("<h2>1. Flip level - current maths vs fixed</h2>")
    rows, wins, n = [], 0, 0
    for d, lst in R["t1"].items():
        for r in lst:
            c, f = r["current"] or {}, r["fixed"] or {}
            rows.append([d, r["t"], r["spot"], c.get("flip"), f.get("flip"), c.get("to_close"), f.get("to_close"),
                         c.get("closest"), f.get("closest")])
            if c.get("closest") is not None and f.get("closest") is not None:
                n += 1
                wins += f["closest"] < c["closest"]
    p.append(tbl(["Day", "Time", "SPX", "Flip now", "Flip fixed", "Now: pts to close", "Fixed: pts to close",
                  "Now: nearest price came", "Fixed: nearest price came"], rows))
    p.append("<div class=read>Current maths prices every 0DTE option as if one hour were left, all day. "
             "The fixed version uses the real minutes to 16:00. Fixed flip was closer to where price actually "
             "traded in <b>%d of %d</b> checks.</div>" % (wins, n))

    # T2
    t2 = R["t2"]
    p.append("<h2>2. How IV really moves when SPX moves (15 min ahead)</h2>")
    rm = t2["rmse"]
    best = min((k for k in rm if rm[k] is not None), key=lambda k: rm[k], default=None)
    p.append(tbl(["Model", "Typical error (vol pts)", "What it assumes"],
                 [["Plain map (sticky strike)", rm.get("plain"), "IV at each strike stays put"],
                  ["Smile button now (sticky moneyness)", rm.get("smile"), "Smile slides along with SPX"],
                  ["Time drift only", rm.get("drift"), "IV drifts on its own clock, ignores SPX"],
                  ["Measured (drift + measured beta)", rm.get("measured"), "Learned from earlier minutes only"]]))
    bl = [[b, v["vol_pts_per_1pct"], v["n"]] for b, v in sorted(t2["beta"].items())]
    p.append(tbl(["Strike zone", "IV change per +1% SPX (vol pts)", "Pairs"], bl))
    p.append("<div class=read>Lowest error = the assumption closest to reality: <b>%s</b>. "
             "Alma's front-end number for comparison: about -1.0 to -1.3 vol pts per +1%%. "
             "%d predictions scored.</div>" % (best, t2["n"]))
    p.append(tbl(["Day", "N", "Plain", "Smile now", "Drift", "Measured"],
                 [[d["day"], d["n"], d["plain"], d["smile"], d["drift"], d["measured"]] for d in t2["per_day"]]))

    # T3
    p.append("<h2>3. Dealer delta on expiring options at 3:30 vs the move to the close</h2>")
    rows, books = [], set()
    for d, x in R["t3"].items():
        for t0, v in x.items():
            books |= set(v["D_bn"])
    books = sorted(books)
    for d, x in R["t3"].items():
        for t0, v in x.items():
            rows.append([d, t0, v["ret_bp"]] + [v["D_bn"].get(b) for b in books])
    p.append(tbl(["Day", "From", "SPX to close (bp)"] + ["%s ($bn)" % b for b in books], rows))
    lines = []
    for b, s in R["t3_sum"].items():
        lines.append("<b>%s</b>: sign right %s of %s, rank match %s" % (b, s["hits"], s["n"],
                     "n/a" if s["rho"] is None else "%+.2f" % s["rho"]))
    p.append("<div class=read>Study with real dealer data: high dealer delta at 3:30 -> SPX up into the close "
             "(17 bp top vs bottom fifth). A book that is closer to real dealer positions should match the sign "
             "and the ranking. %s.<br>Naive OI always reads dealers long delta (long calls + short puts) so its "
             "sign test is meaningless - watch its ranking only.</div>" % "; ".join(lines))

    # T4
    t4 = R["t4"]
    p.append("<h2>4. Which gamma goes with a calmer next 30 minutes</h2>")
    p.append(tbl(["Gamma", "Rank link with next-30-min movement"],
                 [["Today's expiry (0DTE)", t4.get("g0")], ["Longer expiries", t4.get("gw")], ["All together", t4.get("gt")]]))
    p.append("<div class=read>Negative = more dealer gamma, calmer next 30 minutes (what the gate assumes). "
             "Measured within the same time slot each day, so the normal lunch lull doesn't count. %d slots.</div>" % t4.get("n", 0))

    # T5
    p.append("<h2>5. Morning band width</h2>")
    rows, agg = [], {}
    for d, x in R["t5"].items():
        for name in ("IV cone 1sd", "Gap strip 1 straddle", "1.2533 x straddle"):
            v = x.get(name)
            if not v:
                continue
            a = agg.setdefault(name, [0, 0, 0, 0])
            a[0] += 1; a[1] += v["close_in_1"]; a[2] += v["path_in_1"]; a[3] += v["touch_up"] or v["touch_dn"]
            rows.append([d, name, v["width"], "yes" if v["close_in_1"] else "no", "yes" if v["path_in_1"] else "no"])
    p.append(tbl(["Day", "Band", "Width (pts)", "Close inside", "Whole day inside"], rows))
    p.append(tbl(["Band", "Days", "Close inside (theory 68%)", "Whole day inside (theory 37%)", "Touched an edge (theory ~55%)"],
                 [[k, a[0], "%d" % a[1], "%d" % a[2], "%d" % a[3]] for k, a in agg.items()]))
    p.append("<div class=read>One straddle is only about 0.8 standard deviations, so the Gap strip should hold "
             "the close about 58% of the time, not 68%. The IV cone and 1.2533 x straddle should be nearly the same width.</div>")
    return "\n".join(p)


START = "20261028"          # first stored session
STOP = "20261204"           # 30-day scoring window ends after this session (Fri Dec 4 2026)


def score_day(ymd, data, out_root, min_minutes):
    """Everything for one session, cached in alt_days/<ymd>.json so later nights only add the new day."""
    day = datetime.datetime.strptime(ymd, "%Y%m%d").date()
    day_iso = day.isoformat()
    fp = data / ("flow_%s.ndjson" % ymd)
    if not fp.is_file():
        return None, "no flow"
    flow = load_flow_spx(fp, day_iso)
    if len(flow) < min_minutes:
        return None, "only %d SPX minutes" % len(flow)
    path = sorted((t, j["x"]) for t, j in flow.items() if "09:31" <= t <= "16:00")
    close = path[-1][1]
    ex = get_extract(day, data, out_root)
    oi = oi_book(ex) if ex else {}
    week = ((ex or {}).get("week") or {}).get("SPX", {}).get("rows") or []
    ledger = load_ledger(data, ymd, day_iso)
    D = {"day": ymd, "minutes": len(flow), "oi": len(oi), "ledger": bool(ledger),
         "t2": t2_pairs(flow), "t5": t5_day(flow, path, close)}
    if oi:
        D["t1"] = t1_flip(flow, oi, path, close)
        D["t3"] = t3_day(flow, oi, ledger, close)
        D["t4"] = t4_day(flow, oi, week, dict(path))
    return D, None


def tally(days, t2):
    """Per-night old-vs-new verdicts -> 'new beat old on X of Y nights'."""
    V = {}
    # T1 flip: fixed closer to traded prices on most checks that day
    w = n = 0
    for D in days:
        rs = [r for r in D.get("t1") or [] if (r.get("current") or {}).get("closest") is not None
              and (r.get("fixed") or {}).get("closest") is not None]
        if not rs:
            continue
        k = sum(r["fixed"]["closest"] < r["current"]["closest"] for r in rs)
        n += 1
        w += k * 2 > len(rs)
    V["flip"] = (w, n)
    # T2 smile: measured lower 15-min IV error than plain / than the Smile button, per night
    pd_ = [x for x in t2["per_day"] if x.get("measured") is not None]
    V["smile_vs_plain"] = (sum(x["measured"] < x["plain"] for x in pd_), len(pd_))
    V["smile_vs_button"] = (sum(x["measured"] < x["smile"] for x in pd_), len(pd_))
    V["button_vs_plain"] = (sum(x["smile"] < x["plain"] for x in pd_), len(pd_))
    # T3 3:30 dealer delta: sign right per book
    t3 = {}
    for D in days:
        v = (D.get("t3") or {}).get("15:30")
        if not v:
            continue
        for b, d in v["D_bn"].items():
            a = t3.setdefault(b, [0, 0])
            a[1] += 1
            a[0] += (d > 0) == (v["ret_bp"] > 0)
    V["delta330"] = t3
    # T5 bands: close inside +-1 band
    t5 = {}
    for D in days:
        for name in ("IV cone 1sd", "Gap strip 1 straddle", "1.2533 x straddle"):
            v = (D.get("t5") or {}).get(name)
            if v:
                a = t5.setdefault(name, [0, 0])
                a[1] += 1
                a[0] += bool(v["close_in_1"])
    V["band"] = t5
    return V


def verdict_lines(V, t4, stopped):
    L = []
    w, n = V["flip"]
    L.append("Flip: fixed maths closer to where price traded on %d of %d nights" % (w, n))
    a, b, c = V["smile_vs_plain"], V["smile_vs_button"], V["button_vs_plain"]
    L.append("Smile: measured beat plain map on %d of %d nights, beat Smile button on %d of %d (Smile button beat plain %d of %d)"
             % (a[0], a[1], b[0], b[1], c[0], c[1]))
    L.append("3:30 dealer delta called the close: " + ", ".join("%s %d of %d" % (k, v[0], v[1]) for k, v in sorted(V["delta330"].items())))
    L.append("Gate (all nights pooled, more negative = more gamma, calmer next 30 min): 0DTE %s, longer expiries %s, all %s"
             % (t4.get("g0"), t4.get("gw"), t4.get("gt")))
    L.append("Band, close inside 1 sd (theory 68%): " + ", ".join("%s %d of %d" % (k, v[0], v[1]) for k, v in V["band"].items()))
    if stopped:
        L.append("SCORING PERIOD ENDED %s - decide each test and remove the nightly 'alt score' task." % STOP)
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(CVX / "data"))
    ap.add_argument("--out", default=str(HERE.parent / "reports" / "replay"))
    ap.add_argument("--days", help="comma list YYYYMMDD (default: every stored day in the window)")
    ap.add_argument("--min-minutes", type=int, default=300, help="skip days with fewer SPX minutes")
    ap.add_argument("--rescore", action="store_true", help="ignore the per-day cache")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()
    data, out_root = Path(args.data), Path(args.out)
    cache = out_root / "alt_days"
    cache.mkdir(parents=True, exist_ok=True)
    if args.days:
        ymds = [d.strip() for d in args.days.split(",") if d.strip()]
    else:
        ymds = sorted(os.path.basename(p)[5:13] for p in glob.glob(str(data / "flow_2*.ndjson")))
    ymds = [d for d in ymds if START <= d <= STOP]
    from zoneinfo import ZoneInfo
    now_et = datetime.datetime.now(ZoneInfo("America/New_York"))      # NAS containers run on UTC
    today = now_et.strftime("%Y%m%d")
    stopped = today > STOP
    skipped = []
    for ymd in ymds:
        cp = cache / ("%s.json" % ymd)
        if cp.is_file() and not args.rescore:
            continue
        if ymd == today and now_et.hour < 17 and not args.days:
            continue                                     # session not finished
        D, why = score_day(ymd, data, out_root, args.min_minutes)
        if D is None:
            skipped.append("%s %s" % (ymd, why))
            continue
        with open(cp, "w", encoding="utf-8") as fh:
            json.dump(D, fh, separators=(",", ":"))
        print("scored %s: %d minutes, OI strikes %d, ledger %s" % (ymd, D["minutes"], D["oi"], "yes" if D["ledger"] else "no"), flush=True)

    days = []
    for cp in sorted(cache.glob("2*.json")):
        if START <= cp.stem <= STOP:
            days.append(json.load(open(cp, encoding="utf-8")))
    R = {"days": [D["day"] for D in days], "t1": {D["day"]: D["t1"] for D in days if D.get("t1")},
         "t3": {D["day"]: D["t3"] for D in days if D.get("t3")}, "t5": {D["day"]: D["t5"] for D in days}}
    R["t2"] = t2_score([(D["day"], D["t2"]) for D in days])
    s3 = {}
    for d, x in R["t3"].items():
        v = x.get("15:30")
        if not v:
            continue
        for b, Dv in v["D_bn"].items():
            a = s3.setdefault(b, {"D": [], "r": []})
            a["D"].append(Dv); a["r"].append(v["ret_bp"])
    R["t3_sum"] = {b: {"n": len(a["D"]), "hits": sum((Dv > 0) == (r > 0) for Dv, r in zip(a["D"], a["r"])),
                       "rho": spearman(a["D"], a["r"])} for b, a in s3.items()}
    slots = {}
    for D in days:
        for r in D.get("t4") or []:
            slots.setdefault(r["t"], []).append(r)
    t4 = {"n": sum(len(v) for v in slots.values())}
    for key in ("g0", "gw", "gt"):
        vals = [spearman([r[key] for r in rs], [r["rv"] for r in rs]) for rs in slots.values() if len(rs) >= 4]
        vals = [v for v in vals if v is not None]
        t4[key] = round(sum(vals) / len(vals), 2) if vals else None
    R["t4"] = t4
    R["skipped"] = skipped
    V = tally(days, R["t2"])
    R["verdict"] = verdict_lines(V, t4, stopped)
    R["window"] = "%s to %s" % (START, STOP)
    with open(out_root / "alt_scores.json", "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1, default=str)
    head = ("<div class=read><b>Scoring window %s - %s (%d nights so far).</b><br>%s</div>"
            % (START, STOP, len(days), "<br>".join(R["verdict"])))
    body = html_report(R)
    k = body.index("<h2>")
    with open(out_root / "alt_scores.html", "w", encoding="utf-8") as fh:
        fh.write(body[:k] + "<h2>Verdict so far - new vs old</h2>" + head + body[k:])
    for line in R["verdict"]:
        print(line)
    if skipped:
        print("skipped: " + "; ".join(skipped))
    print("wrote %s" % (out_root / "alt_scores.html"))
    if not args.no_telegram:
        try:
            sys.path.insert(0, str(HERE))
            import cvx_replay
            txt = "<b>New vs old calculations</b> (%d nights, window to %s)\n" % (len(days), STOP) + "\n".join(
                "- " + x for x in R["verdict"])
            cvx_replay.telegram(txt, [(str(out_root / "alt_scores.html"), "New vs old - full table")])
        except Exception as exc:
            print("telegram failed: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
