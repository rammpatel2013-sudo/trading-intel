"""Live cockpit read for Claude (2026-11-13).

The cockpit's live numbers are files on the NAS (written every minute by the cvxmap
containers, served by serve_cvx.py on :8899) - they never go into the database, and
the old DB jobs behind get_live_gex / get_intraday_flow / get_delta_flow are switched
off. These tools read the SAME files the cockpit reads, so Claude sees what Mithil sees:

* ``get_cockpit_live``    - live.json: price, 0DTE expected move, dealer gamma (sign at
  spot, flip, walls), today's 0DTE flow (calls/puts bought vs sold), live 0DTE skew, term.
* ``get_cockpit_surface`` - surface_live_SPX.json: gamma by expiry + ATM IV by expiry +
  the measured spot-vol beta.

Read-only HTTP GETs on the LAN (or the Tailscale address); no vendor call, no DB, no
load on the NAS beyond serving a file. Descriptive, never a signal (rule 4).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import httpx

from trading_intel.config import Settings

_TIMEOUT = 6.0


# ============================================================ fetch
def fetch_json(settings: Settings, name: str) -> tuple[dict | None, str | None]:
    """GET <cockpit>/<name>; the LAN address first, then the Tailscale one."""
    errs = []
    for base in (getattr(settings, "COCKPIT_URL", ""), getattr(settings, "COCKPIT_URL_ALT", "")):
        if not base:
            continue
        url = base.rstrip("/") + "/" + name
        try:
            r = httpx.get(url, timeout=_TIMEOUT, params={"_": int(datetime.now().timestamp())})
            r.raise_for_status()
            return r.json(), None
        except Exception as exc:  # noqa: BLE001 - try the next address, report both
            errs.append(f"{base}: {type(exc).__name__}")
    return None, "; ".join(errs) or "no cockpit address set"


# ============================================================ maths
def _gamma(s: float, k: float, t: float, iv: float) -> float:
    """Black-Scholes gamma per 1 unit of spot (r = q = 0, fine for a 0DTE profile)."""
    if s <= 0 or k <= 0 or t <= 0 or iv <= 0:
        return 0.0
    sd = iv * math.sqrt(t)
    d1 = (math.log(s / k) + 0.5 * sd * sd) / sd
    return math.exp(-0.5 * d1 * d1) / (math.sqrt(2 * math.pi) * s * sd)


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def gamma_profile(rows: list[dict], spot: float, t: float, iv0: float, step: float) -> list[tuple[float, float]]:
    """Naive dealer gamma ($bn of delta per 1% move) on a price grid: dealers long calls, short puts
    (the cockpit's OI basis), each strike at its own IV (ATM IV where missing)."""
    out = []
    lo, hi = spot * 0.975, spot * 1.025
    s = math.floor(lo / step) * step
    while s <= hi:
        g = 0.0
        for r in rows:
            k = _num(r.get("k"))
            if not k:
                continue
            civ = _num(r.get("cIV")) or iv0
            piv = _num(r.get("pIV")) or iv0
            g += (_num(r.get("cOI")) or 0) * _gamma(s, k, t, civ) - (_num(r.get("pOI")) or 0) * _gamma(s, k, t, piv)
        out.append((s, g * 100.0 * s * s * 0.01 / 1e9))
        s += step
    return out


def _flip(profile: list[tuple[float, float]], spot: float) -> float | None:
    best = None
    for (s0, g0), (s1, g1) in zip(profile, profile[1:]):
        if g0 == 0 or g0 * g1 < 0:
            x = s0 if g0 == 0 else s0 + (s1 - s0) * (-g0) / (g1 - g0)
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return round(best, 1) if best is not None else None


def _near_delta(rows: list[dict], key_d: str, key_iv: str, target: float) -> tuple[float | None, float | None]:
    cand = [(abs((_num(r.get(key_d)) or 9) - target), r) for r in rows if _num(r.get(key_iv))]
    if not cand:
        return None, None
    d, r = min(cand, key=lambda x: x[0])
    return (_num(r.get("k")), _num(r.get(key_iv))) if d < 0.08 else (None, None)


def _sum(rows: list[dict], key: str) -> float | None:
    vals = [_num(r.get(key)) for r in rows]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def summarize_live(live: dict, symbol: str = "SPX", now: datetime | None = None) -> dict[str, Any]:
    sym = symbol.upper()
    d = live.get(sym) or {}
    rows = d.get("rows") or []
    spot = _num(d.get("spot"))
    if not rows or not spot:
        return {"symbol": sym, "found": False, "ts": live.get("ts"), "reason": "no rows for this symbol in live.json"}
    ts = str(live.get("ts") or "")
    age = None
    if now is not None and ts:
        try:
            age = round((now - datetime.fromisoformat(ts[:19])).total_seconds() / 60.0, 1)
        except ValueError:
            age = None
    iv0 = _num(d.get("iv")) or 0.0
    t = _num(d.get("T_greeks")) or 1.0 / 365.0
    step = _num(d.get("step")) or 5.0
    prof = gamma_profile(rows, spot, t, iv0, step)
    g_now = min(prof, key=lambda p: abs(p[0] - spot))[1] if prof else None
    flip = _flip(prof, spot)
    cw = max(rows, key=lambda r: _num(r.get("cGEX")) or 0)
    pw = max(rows, key=lambda r: _num(r.get("pGEX")) or 0)
    straddle = _num(d.get("straddle"))

    # today's 0DTE flow - measured buy / sell where the vendor sent it
    def prem(vkey: str, mkey: str) -> float:
        return sum((_num(r.get(vkey)) or 0) * (_num(r.get(mkey)) or 0) * 100 for r in rows) / 1e6

    cb, cs, pb, ps = (_sum(rows, k) for k in ("cVb", "cVs", "pVb", "pVs"))
    flow: dict[str, Any] = {"calls_traded": _sum(rows, "cV"), "puts_traded": _sum(rows, "pV"),
                            "call_premium_musd": round(_sum(rows, "cPrem") or 0, 1),
                            "put_premium_musd": round(_sum(rows, "pPrem") or 0, 1)}
    if None not in (cb, cs, pb, ps):
        bull = prem("cVb", "cM") + prem("pVs", "pM")
        bear = prem("pVb", "pM") + prem("cVs", "cM")
        flow.update({"calls_bought": cb, "calls_sold": cs, "puts_bought": pb, "puts_sold": ps,
                     "bullish_premium_musd": round(bull, 1), "bearish_premium_musd": round(bear, 1),
                     "lean": "bullish" if bull > 1.15 * bear else "bearish" if bear > 1.15 * bull else "balanced",
                     "lean_rule": "bullish = calls bought + puts sold; bearish = puts bought + calls sold (premium, $M)"})
    top = sorted(rows, key=lambda r: (_num(r.get("cPrem")) or 0) + (_num(r.get("pPrem")) or 0), reverse=True)[:5]
    flow["top_strikes"] = [{"k": _num(r.get("k")), "call_prem_musd": round(_num(r.get("cPrem")) or 0, 2),
                            "put_prem_musd": round(_num(r.get("pPrem")) or 0, 2)} for r in top]

    # live 0DTE skew from the chain's own deltas
    _, p25 = _near_delta(rows, "pD", "pIV", -0.25)
    _, c25 = _near_delta(rows, "cD", "cIV", 0.25)
    _, p10 = _near_delta(rows, "pD", "pIV", -0.10)
    skew = {"atm_iv": round(iv0, 4) or None, "put25_iv": p25, "call25_iv": c25, "put10_iv": p10,
            "rr25_pts": round((p25 - c25) * 100, 2) if p25 and c25 else None,
            "put10_over_atm_pts": round((p10 - iv0) * 100, 2) if p10 and iv0 else None}

    ser = d.get("ser") or []
    trend = None
    if len(ser) >= 2:
        a, b = ser[0], ser[-1]
        trend = {"from": a[0], "to": b[0], "net_gex_then": round(a[1], 3), "net_gex_now": round(b[1], 3),
                 "net_dex_then": round(a[2], 3), "net_dex_now": round(b[2], 3)}
    tl = d.get("tl") or []
    path = [x[1] for x in tl if isinstance(x, list) and len(x) > 1 and _num(x[1])]
    vix = live.get("VIX") or {}
    return {
        "symbol": sym, "found": True, "ts": ts, "age_min": age, "spot": spot, "open": _num(d.get("open")),
        "pct_from_open": round((_num(d.get("pct")) or 0) * 100, 2), "mins_to_close": _num(d.get("mins_to_close")),
        "recent_high": max(path) if path else None, "recent_low": min(path) if path else None,
        "recent_window": f"{tl[0][0]}-{tl[-1][0]}" if tl else None,
        "expected_move": {"straddle": straddle, "lower": spot - straddle if straddle else None,
                          "upper": spot + straddle if straddle else None, "expiry": d.get("exp")},
        "gamma": {"book": "0DTE, strikes within +-20 steps; naive sign (dealers long calls, short puts) = cockpit OI basis",
                  "at_spot_bn_per_1pct": round(g_now, 3) if g_now is not None else None,
                  "regime": None if g_now is None else ("long gamma - moves get damped" if g_now > 0 else "short gamma - moves can run"),
                  "flip": flip, "call_wall": _num(cw.get("k")), "put_wall": _num(pw.get("k")),
                  "net_gex_bn": round(sum(_num(r.get("gex")) or 0 for r in rows), 3),
                  "net_dex_bn": round(sum(_num(r.get("dex")) or 0 for r in rows), 3)},
        "gamma_trend": trend,
        "flow_0dte": flow,
        "skew_0dte": skew,
        "term": [{"dte": x.get("dte"), "atm_iv": _num(x.get("iv")), "move_pct": round((_num(x.get("move")) or 0) * 100, 2)}
                 for x in (d.get("term") or [])[:6]],
        "vix": _num(vix.get("spot")),
        "note": "Live cockpit numbers (same files the cockpit reads). Descriptive, not a signal.",
    }


def summarize_surface(sf: dict) -> dict[str, Any]:
    ks = sf.get("k") or []
    ex = sf.get("ex") or []
    spot = _num(sf.get("x"))
    g = ((sf.get("g") or {}).get("oi")) or []
    va = ((sf.get("va") or {}).get("oi")) or []
    if not ks or not ex:
        return {"found": False, "reason": "surface file has no strikes / expiries"}
    near = [i for i, k in enumerate(ks) if spot and abs(float(k) - spot) <= 0.01 * spot]
    rows = []
    for j, e in enumerate(ex[:10]):
        gj = g[j] if j < len(g) else []
        vj = va[j] if j < len(va) else []
        tot = sum(v or 0 for v in gj)
        rows.append({"expiry": e.get("e"), "dte": e.get("dte"), "fam": e.get("fam"), "atm_iv": _num(e.get("iv")),
                     "gamma_total_bn": round(tot, 3),
                     "gamma_near_spot_bn": round(sum((gj[i] or 0) for i in near if i < len(gj)), 3),
                     "vanna_total_bn": round(sum(v or 0 for v in vj), 3)})
    big = max(rows, key=lambda r: abs(r["gamma_total_bn"]), default=None)
    return {"found": True, "time": sf.get("t"), "day": sf.get("day"), "spot": spot,
            "spot_vol_beta": _num(sf.get("beta")), "beta_src": sf.get("beta_src"),
            "beta_meaning": "vol points per +1% SPX move (negative = vol rises as SPX falls)",
            "by_expiry": rows, "biggest_gamma_expiry": big["expiry"] if big else None,
            "units": "gamma $bn of delta per 1% move, vanna $bn per 1 vol pt; naive OI book (dealers long calls, short puts)"}


# ============================================================ tools
def get_cockpit_live(settings: Settings, symbol: str = "SPX") -> dict[str, Any]:
    live, err = fetch_json(settings, "live.json")
    if live is None:
        return {"symbol": symbol.upper(), "found": False, "reason": f"cockpit not reachable ({err})"}
    from trading_intel.timeutils import eastern_now

    return summarize_live(live, symbol, now=eastern_now())


def get_cockpit_surface(settings: Settings, symbol: str = "SPX") -> dict[str, Any]:
    sf, err = fetch_json(settings, f"surface_live_{symbol.upper()}.json")
    if sf is None:
        return {"symbol": symbol.upper(), "found": False, "reason": f"surface file not reachable ({err})"}
    out = summarize_surface(sf)
    out["symbol"] = symbol.upper()
    return out
