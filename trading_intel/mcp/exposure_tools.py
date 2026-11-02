"""MCP readers for Ship 2 #88 — Exposure Board tab.

Three tools powering the 6-panel exposure board:

    get_exposure_by_strike  — Net DEX + GEX by strike, call/put split
    get_vol_drift           — Intraday realized vol vs 0DTE ATM IV
    get_net_premium_drift   — Cumulative call $ vs put $ flow

All read from banked NAS data. READ-ONLY, no Convex calls, descriptor only
(FlashAlpha rule 4). No cloud LLM calls (CLAUDE.md rule 7).

Wire in server.py:
    from trading_intel.mcp import exposure_tools as ext

    @mcp.tool()
    def get_exposure_by_strike(symbol: str = "SPX", dte_max: int = 60) -> dict[str, Any]:
        with session_scope() as session:
            return ext.get_exposure_by_strike(session, symbol, dte_max=dte_max)
    ... (same pattern for each)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.mcp.tools import _iso_day, _iso_ts
from trading_intel.memory.models import (
    GreeksSnapshot,
    LiveGex,
    OiChainEod,
)
from trading_intel.timeutils import eastern_now, live_expiry_floor


# ---------------------------------------------------------------------------
# Panel 1+2+3: DEX/GEX by strike (combined — the page splits the view)
# ---------------------------------------------------------------------------

def get_exposure_by_strike(
    session: Session,
    symbol: str = "SPX",
    *,
    dte_max: int = 60,
) -> dict[str, Any]:
    """Per-strike dealer delta exposure (DEX) and gamma exposure (GEX).

    Reads the latest ``oi_chain_eod`` snapshot and returns per-strike:
    - net_dex: dxoi summed across C+P (dealer delta exposure)
    - call_dex / put_dex: dxoi split by side
    - net_gex: gxoi summed across C+P (dealer gamma exposure)
    - call_gex / put_gex: gxoi split by side

    The call_wall / put_wall / gex_flip are derived from the same data.
    Units: vendor-scaled (Convex gxoi/dxoi). Descriptor only (rule 4).
    """
    sym = symbol.upper()

    # Latest snapshot timestamp
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == sym, OiChainEod.source.like("convex%"))
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()

    spot_row = session.execute(
        select(GreeksSnapshot.spot)
        .where(GreeksSnapshot.symbol == sym, GreeksSnapshot.source == "convex")
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).first()
    spot = float(spot_row[0]) if spot_row and spot_row[0] is not None else None

    if ts is None or spot is None:
        return {"symbol": sym, "found": False, "reason": "no chain or spot"}

    book_day = ts.date() if isinstance(ts, datetime) else ts
    floor = live_expiry_floor(book_day)

    rows = session.execute(
        select(
            OiChainEod.strike,
            OiChainEod.cp,
            OiChainEod.dxoi,
            OiChainEod.gxoi,
            OiChainEod.oi,
            OiChainEod.expiry,
            OiChainEod.dte,
        ).where(
            OiChainEod.symbol == sym,
            OiChainEod.source.like("convex%"),
            OiChainEod.ts == ts,
            OiChainEod.expiry >= floor,
            OiChainEod.dte <= dte_max,
        )
    ).all()

    if not rows:
        return {"symbol": sym, "found": False, "reason": "empty chain"}

    # Aggregate by strike
    strikes: dict[float, dict[str, float]] = {}
    for r in rows:
        k = float(r.strike)
        if k not in strikes:
            strikes[k] = {
                "net_dex": 0.0, "call_dex": 0.0, "put_dex": 0.0,
                "net_gex": 0.0, "call_gex": 0.0, "put_gex": 0.0,
                "total_oi": 0,
            }
        s = strikes[k]
        dx = float(r.dxoi) if r.dxoi is not None else 0.0
        gx = float(r.gxoi) if r.gxoi is not None else 0.0
        oi_val = int(r.oi) if r.oi is not None else 0
        s["net_dex"] += dx
        s["net_gex"] += gx
        s["total_oi"] += oi_val
        if str(r.cp).upper().startswith("C"):
            s["call_dex"] += dx
            s["call_gex"] += gx
        else:
            s["put_dex"] += dx
            s["put_gex"] += gx

    # Sort by strike, filter to a reasonable range around spot (±15%)
    lo = spot * 0.85
    hi = spot * 1.15
    sorted_strikes = sorted(
        [(k, v) for k, v in strikes.items() if lo <= k <= hi],
        key=lambda x: x[0],
    )

    series = [
        {
            "strike": k,
            "net_dex": round(v["net_dex"], 2),
            "call_dex": round(v["call_dex"], 2),
            "put_dex": round(v["put_dex"], 2),
            "net_gex": round(v["net_gex"], 2),
            "call_gex": round(v["call_gex"], 2),
            "put_gex": round(v["put_gex"], 2),
            "total_oi": v["total_oi"],
        }
        for k, v in sorted_strikes
    ]

    # Derived: max call GEX strike (call wall), max put GEX strike (put wall)
    call_wall = max(sorted_strikes, key=lambda x: x[1]["call_gex"], default=None)
    put_wall = max(sorted_strikes, key=lambda x: x[1]["put_gex"], default=None)

    # GEX flip: zero-crossing of cumulative net_gex near spot
    gex_vals = [(k, v["net_gex"]) for k, v in sorted_strikes]
    gex_flip = None
    for i in range(1, len(gex_vals)):
        k0, g0 = gex_vals[i - 1]
        k1, g1 = gex_vals[i]
        if g0 < 0 <= g1:
            gex_flip = round(k0 + (k1 - k0) * (-g0) / (g1 - g0), 1)

    return {
        "symbol": sym,
        "found": True,
        "as_of": _iso_ts(ts),
        "spot": round(spot, 2),
        "dte_max": dte_max,
        "call_wall": call_wall[0] if call_wall else None,
        "put_wall": put_wall[0] if put_wall else None,
        "gex_flip": gex_flip,
        "strikes": series,
        "count": len(series),
        "units": "vendor-scaled dxoi/gxoi",
    }


# ---------------------------------------------------------------------------
# Panel 5: Volatility Drift — intraday realized vol vs 0DTE ATM IV
# ---------------------------------------------------------------------------

def get_vol_drift(
    session: Session,
    symbol: str = "SPX",
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Intraday volatility drift: realized vol from per-minute spot vs 0DTE ATM IV.

    Uses ``live_gex`` (intraday spot snapshots at ~1min cadence) to compute
    intraday realized vol, and compares it to the 0DTE ATM IV from the morning
    chain. Shows whether the tape is moving more or less than implied.

    Pass ``as_of`` to override the date (defaults to eastern_now().date()).
    Descriptor only (rule 4).
    """
    sym = symbol.upper()

    # Get today's intraday spot ticks from live_gex
    today = as_of or eastern_now().date()
    ticks = session.execute(
        select(LiveGex.ts, LiveGex.spot)
        .where(
            LiveGex.symbol == sym,
            LiveGex.ts >= datetime.combine(today, datetime.min.time()),
        )
        .order_by(LiveGex.ts)
    ).all()

    if len(ticks) < 10:
        return {"symbol": sym, "found": False, "reason": "insufficient intraday ticks"}

    spots = np.array([float(t.spot) for t in ticks if t.spot is not None])
    if len(spots) < 10:
        return {"symbol": sym, "found": False, "reason": "insufficient spot data"}

    # Intraday log returns
    log_ret = np.diff(np.log(spots))

    # Annualized intraday RV: scale by sqrt(252 * minutes_per_day / interval_minutes)
    # Approximate: ~390 minutes in a trading day, ticks at ~1 min intervals
    n_ticks = len(log_ret)
    intervals_per_day = 390  # 6.5 hours * 60
    annual_scale = np.sqrt(252 * intervals_per_day)
    intraday_rv = float(np.std(log_ret, ddof=1) * annual_scale) if n_ticks > 1 else None

    # 0DTE ATM IV from OI chain
    atm_iv = None
    gs = session.execute(
        select(GreeksSnapshot.atm_iv)
        .where(GreeksSnapshot.symbol == sym, GreeksSnapshot.source == "convex")
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if gs is not None:
        atm_iv = float(gs)

    # Drift = realized − implied
    drift = round(intraday_rv - atm_iv, 4) if intraday_rv is not None and atm_iv is not None else None

    return {
        "symbol": sym,
        "found": True,
        "as_of": _iso_day(today),
        "n_ticks": n_ticks,
        "intraday_rv_annualized": round(intraday_rv, 4) if intraday_rv is not None else None,
        "atm_iv_0dte": round(atm_iv, 4) if atm_iv is not None else None,
        "vol_drift": drift,
        "drift_label": (
            "tape hotter than implied" if drift and drift > 0.02
            else "tape cooler than implied" if drift and drift < -0.02
            else "in line" if drift is not None
            else None
        ),
        "units": "annualized vol (decimal)",
    }


# ---------------------------------------------------------------------------
# Panel 6: Net Drift Premium — cumulative call $ vs put $ flow
# ---------------------------------------------------------------------------

def get_net_premium_drift(
    session: Session,
    symbol: str = "SPX",
    *,
    days: int = 5,
) -> dict[str, Any]:
    """Net premium drift: cumulative call vs put notional from flow snapshots.

    Uses ``flow_snapshots`` (one row per 5-min cycle with call/put notional
    and net_premium). Takes the last snapshot per day over ``days`` trailing
    days to show whether the tape's dollar flow is call- or put-heavy.

    Descriptor only (rule 4).
    """
    from trading_intel.memory.models import FlowSnapshot

    sym = symbol.upper()
    days_c = max(1, min(int(days), 30))

    # Latest snapshot per day — func.date() works on both SQLite and Postgres
    day_col = func.date(FlowSnapshot.ts)
    rows = session.execute(
        select(
            day_col.label("day"),
            func.max(FlowSnapshot.ts).label("last_ts"),
        )
        .where(FlowSnapshot.symbol == sym)
        .group_by(day_col)
        .order_by(day_col.desc())
        .limit(days_c)
    ).all()

    if not rows:
        return {"symbol": sym, "found": False, "reason": "no flow_snapshot data"}

    # Fetch the actual rows at those timestamps
    ts_set = [r.last_ts for r in rows]
    detail_rows = session.execute(
        select(
            FlowSnapshot.ts,
            FlowSnapshot.call_notional,
            FlowSnapshot.put_notional,
            FlowSnapshot.net_premium,
        )
        .where(FlowSnapshot.symbol == sym, FlowSnapshot.ts.in_(ts_set))
        .order_by(FlowSnapshot.ts)
    ).all()

    series = []
    cum_call = 0.0
    cum_put = 0.0
    for r in detail_rows:
        cn = float(r.call_notional) if r.call_notional is not None else 0.0
        pn = float(r.put_notional) if r.put_notional is not None else 0.0
        cum_call += cn
        cum_put += pn
        series.append({
            "date": _iso_day(r.ts.date() if isinstance(r.ts, datetime) else r.ts),
            "call_notional": round(cn, 2),
            "put_notional": round(pn, 2),
            "net": round(cn - pn, 2),
            "cum_call": round(cum_call, 2),
            "cum_put": round(cum_put, 2),
            "cum_net": round(cum_call - cum_put, 2),
        })

    latest = series[-1] if series else {"cum_net": 0}
    return {
        "symbol": sym,
        "found": True,
        "as_of": _iso_ts(detail_rows[-1].ts) if detail_rows else None,
        "days": days_c,
        "cum_call_notional": latest.get("cum_call", 0),
        "cum_put_notional": latest.get("cum_put", 0),
        "cum_net": latest.get("cum_net", 0),
        "bias": (
            "call-heavy" if latest.get("cum_net", 0) > 0
            else "put-heavy" if latest.get("cum_net", 0) < 0
            else "balanced"
        ),
        "series": series,
        "count": len(series),
        "units": "$ notional (vendor-scaled)",
    }
