# ruff: noqa: E501
"""Vomma / zomma per expiry bucket vs its own baseline (#60 group, 2026-11-13 overnight; TTWD idea #4).

For one stored SPX book (oi_chain_eod: expiry, strike, call/put, OI, IV) and the spot at that time, sums the
dealer exposure per DTE bucket on the NAIVE book (dealers long calls, short puts - the same sign as the profile):
  gex_bn    dealer $ gamma per 1% move, $bn
  vomma_mn  change in dealer vega ($ per vol pt) when IV rises 1 pt, $M   (vega convexity: who gets paid when vol jumps)
  zomma_bn  change in dealer $ gamma per 1% when IV rises 1 pt, $bn      (how much a vol move redraws the gamma map)
Flags over the stored history (descriptors, rule 4):
  * hedge-tenor vomma (31-90 DTE) >= 1.45x its 20-book median while total |gex| shrinks vs that median
  * the nearest monthly expiry's |zomma| at <= half of what it was 5 books ago (zomma bleeding out into OpEx)
Black-Scholes, r = 0, each contract's own IV, calendar time to the 16:00 close.
"""

from __future__ import annotations

import math
from datetime import date, datetime

BUCKETS = (("0-7", 0, 7), ("8-30", 8, 30), ("31-90", 31, 90), ("91+", 91, 10000))
VOMMA_X = 1.45


def _greeks(s: float, k: float, sig: float, t: float) -> tuple[float, float, float, float]:
    """gamma, vega (per 1.00 vol), vomma (dvega/dsig), zomma (dgamma/dsig) per share."""
    st = sig * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sig * sig * t) / st
    d2 = d1 - st
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    gamma = pdf / (s * st)
    vega = s * pdf * math.sqrt(t)
    return gamma, vega, vega * d1 * d2 / sig, gamma * (d1 * d2 - 1.0) / sig


def bucket_of(days: int) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= days <= hi:
            return name
    return "91+"


def exposures(rows: list[tuple], spot: float, asof: datetime) -> dict:
    """rows = [(expiry date, strike, 'C'|'P', oi, iv)]. Returns {"buckets": {name: sums}, "expiries": {iso: sums}}."""
    zero = lambda: {"gex_bn": 0.0, "vomma_mn": 0.0, "zomma_bn": 0.0, "oi": 0.0}  # noqa: E731
    bk = {n: zero() for n, _, _ in BUCKETS}
    ex: dict[str, dict] = {}
    for e, k, cp, oi, iv in rows:
        if not (oi and oi > 0 and iv and iv > 0 and k > 0):
            continue
        close = datetime.combine(e, datetime.min.time()).replace(hour=16)
        t = (close - asof).total_seconds() / (365.0 * 86400)
        if t <= 1e-4:
            continue
        g, _v, vo, zo = _greeks(spot, float(k), float(iv), t)
        sign = 1.0 if str(cp).upper().startswith("C") else -1.0
        n = sign * float(oi) * 100.0
        gex = n * g * spot * spot * 0.01 / 1e9
        vom = n * vo / 1e4 / 1e6  # per vol pt squared: /100 for vega per pt, /100 for the 1-pt bump
        zom = n * zo * spot * spot * 0.01 / 100.0 / 1e9
        days = (e - asof.date()).days
        for d in (bk[bucket_of(days)], ex.setdefault(e.isoformat(), zero())):
            d["gex_bn"] += gex
            d["vomma_mn"] += vom
            d["zomma_bn"] += zom
            d["oi"] += float(oi)
    rnd = lambda d: {k: round(v, 4) for k, v in d.items()}  # noqa: E731
    return {
        "buckets": {k: rnd(v) for k, v in bk.items()},
        "expiries": {k: rnd(v) for k, v in sorted(ex.items())},
    }


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def nearest_monthly(expiries: list[str], asof: date) -> str | None:
    """First 3rd-Friday expiry on or after asof."""
    for e in sorted(expiries):
        d = date.fromisoformat(e)
        if d >= asof and d.weekday() == 4 and 15 <= d.day <= 21:
            return e
    return None


def flags(history: list[dict]) -> dict:
    """history = [{"date", "buckets", "expiries"}] oldest first (one per stored book)."""
    if not history:
        return {"found": False}
    cur = history[-1]
    base = history[-21:-1]
    out: dict = {"found": True, "date": cur["date"], "n_hist": len(base), "flags": []}
    vh = cur["buckets"]["31-90"]["vomma_mn"]
    gex_now = sum(b["gex_bn"] for b in cur["buckets"].values())
    out["vomma_hedge_mn"] = round(vh, 3)
    out["gex_total_bn"] = round(gex_now, 3)
    if len(base) >= 10:
        mv = _median([abs(h["buckets"]["31-90"]["vomma_mn"]) for h in base])
        mg = _median([abs(sum(b["gex_bn"] for b in h["buckets"].values())) for h in base])
        out["vomma_vs_base"] = round(abs(vh) / mv, 2) if mv else None
        out["gex_vs_base"] = round(abs(gex_now) / mg, 2) if mg else None
        if (
            out["vomma_vs_base"]
            and out["vomma_vs_base"] >= VOMMA_X
            and out["gex_vs_base"] is not None
            and out["gex_vs_base"] < 1.0
        ):
            out["flags"].append(
                f"hedge-tenor vomma {out['vomma_vs_base']}x its 20-book median while |gamma| is {out['gex_vs_base']}x - "
                "vol convexity building as the gamma cushion thins"
            )
    asof = date.fromisoformat(cur["date"][:10])
    m = nearest_monthly(list(cur["expiries"]), asof)
    out["monthly"] = m
    if m and len(history) >= 6 and m in history[-6].get("expiries", {}):
        z0, z1 = abs(history[-6]["expiries"][m]["zomma_bn"]), abs(cur["expiries"][m]["zomma_bn"])
        out["monthly_zomma_ratio_5"] = round(z1 / z0, 2) if z0 else None
        if z0 and z1 <= 0.5 * z0:
            out["flags"].append(
                f"{m} monthly zomma at {out['monthly_zomma_ratio_5']}x of 5 books ago - its gamma stops reacting to vol into OpEx"
            )
    return out
