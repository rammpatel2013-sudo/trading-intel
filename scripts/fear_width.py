"""fear_width.py - created 2026-11-22. "Is this fear broad or narrow?" for the Risk page.

Implied correlation (Cboe COR1M) against the VIX, every close since COR1M began (2006-01):
    expected  = COR1M on days of the last 4 years with a VIX within +-3 pts of today's (band widens below 15 days);
                4 years because stocks move together far less than they did in 2006-2016 (an all-history read is
                kept too)
    pct       = where today's COR1M sits among those days (0 = lowest, 100 = highest)
    verdict   = Narrow (pct <= 20: the index is scared but stocks are NOT moving as one, the stress sits in a few
                places) / Broad (pct >= 80: everything is moving together) / Normal mix
    gap       = COR1M minus the median COR1M for that VIX level (1-pt VIX bins, last 4 years), per day, last
                year - how narrow or broad the fear has been lately
    first touches = first close with VIX >= 30 after 120 sessions without one, each with its correlation read and
                what SPX did next (20 / 60 sessions, worst close within 20). Tiny n: a list, not a rule.
Descriptor only (CLAUDE.md rule 4). Pure functions: {date: close} dicts in, one dict out.
"""
from __future__ import annotations

from bisect import bisect_left

FIRST_VIX, QUIET, BAND, MIN_N, ERA = 30.0, 120, 3.0, 15, 1008   # ERA = 4 years of sessions


def _pct(xs: list, v: float) -> float:
    xs = sorted(xs)
    return 100.0 * bisect_left(xs, v) / len(xs) if xs else 50.0


def _q(xs: list, f: float):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(f * (len(xs) - 1) + 0.5))] if xs else None


def _near(vs: list, cs: list, v: float, upto: int, frm: int = 0) -> list:
    """COR1M on days frm..upto-1 whose VIX is within the band of v (band widens until MIN_N days)."""
    band = BAND
    while True:
        out = [cs[i] for i in range(max(0, frm), upto) if abs(vs[i] - v) <= band]
        if len(out) >= MIN_N or band > 25:
            return out
        band += 1.0


def verdict(p: float) -> str:
    return "Narrow" if p <= 20 else "Broad" if p >= 80 else "Normal mix"


def build(vix: dict, cor: dict, spx: dict) -> dict:
    dates = sorted(set(vix) & set(cor) & set(spx))
    if len(dates) < 500:
        return {}
    V = [vix[d] for d in dates]
    C = [cor[d] for d in dates]
    P = [spx[d] for d in dates]
    n = len(dates)
    t = n - 1
    # stocks have moved together less and less since ~2017 (COR1M / VIX ~2.8 in 2012, ~0.6 in 2026), so the
    # read compares with the last 4 years; the all-history read is shown next to it.
    near = _near(V, C, V[t], t, t - ERA)
    p = _pct(near, C[t])
    near_all = _near(V, C, V[t], t)
    # median COR1M per 1-pt VIX bin (last 4 years) -> the gap line
    bins: dict = {}
    for v, c in zip(V[max(0, n - ERA):], C[max(0, n - ERA):]):
        bins.setdefault(int(v), []).append(c)
    med = {}
    for b in range(0, int(max(V)) + 2):
        pool = [c for k in range(b - 1, b + 2) for c in bins.get(k, [])]
        if len(pool) >= 8:
            med[b] = _q(pool, 0.5)
    gap = [None if int(V[i]) not in med else round(C[i] - med[int(V[i])], 1) for i in range(max(0, n - 252), n)]

    def fwd(i, k):
        return None if i + k >= n else round((P[i + k] / P[i] - 1) * 100, 1)

    def worst(i, k=20):
        w = P[i + 1:min(n, i + k + 1)]
        return None if not w else round((min(w) / P[i] - 1) * 100, 1)

    touches, last_hi = [], -10 ** 9
    for i in range(n):
        if V[i] >= FIRST_VIX:
            if i - last_hi > QUIET:
                pi = _pct(_near(V, C, V[i], i, i - ERA) if i > 250 else C[:i] or [C[i]], C[i])
                touches.append({"date": dates[i], "vix": round(V[i], 1), "cor1m": round(C[i], 1),
                                "pct": round(pi), "read": verdict(pi), "r20": fwd(i, 20), "r60": fwd(i, 60),
                                "worst20": worst(i)})
            last_hi = i
    pts = [[round(V[i], 1), round(C[i], 1)] for i in range(n) if i % 4 == 0 or V[i] >= 25]
    return {"asof": dates[t], "vix": round(V[t], 2), "cor1m": round(C[t], 2), "pct": round(p),
            "n_near": len(near), "near_q25": round(_q(near, 0.25), 1), "near_med": round(_q(near, 0.5), 1),
            "near_q75": round(_q(near, 0.75), 1), "read": verdict(p), "start": dates[0],
            "pct_all": round(_pct(near_all, C[t])), "n_all": len(near_all), "era_from": dates[max(0, t - ERA)],
            "gap": gap, "gap_dates": dates[max(0, n - 252):], "points": pts, "touches": touches}
