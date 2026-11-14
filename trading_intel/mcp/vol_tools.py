"""MCP readers for Ship 2 vol/correlation/color descriptors.

Four read-only tool functions:

    get_rv_iv_box       — #94 Carroll's RV/IV box (VIX tab)
    get_spot_vol_beta   — #90 spot-vol-beta gauge (VIX tab)
    get_implied_correlation — #95 implied-correlation panel (VIX tab)
    get_color_at_spot   — #87 Color EOD descriptor (Map tab)

All are regime descriptors, not signals (FlashAlpha rule 4). Each function
takes a ``Session`` + args and returns a JSON-serialisable dict. No Convex
calls, no writes, no cloud LLM.

Wire in server.py:
    from trading_intel.mcp import vol_tools as vt

    @mcp.tool()
    def get_rv_iv_box(days: int = 60) -> dict[str, Any]:
        with session_scope() as session:
            return vt.get_rv_iv_box(session, days=days)

    @mcp.tool()
    def get_spot_vol_beta(window: int = 21, days: int = 60) -> dict[str, Any]:
        with session_scope() as session:
            return vt.get_spot_vol_beta(session, window=window, days=days)

    @mcp.tool()
    def get_implied_correlation(days: int = 60) -> dict[str, Any]:
        with session_scope() as session:
            return vt.get_implied_correlation(session, days=days)

    @mcp.tool()
    def get_color_at_spot(symbol: str = "SPX", hours_ahead: float = 6.5) -> dict[str, Any]:
        with session_scope() as session:
            return vt.get_color_at_spot(session, symbol, hours_ahead=hours_ahead)
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.exposure_profile import greek_profiles
from trading_intel.memory.models import (
    GreeksSnapshot,
    IndexSkewDaily,
    IvTenorSnapshot,
    OiChainEod,
    QuoteDaily,
    VixData,
)
from trading_intel.mcp.tools import _iso_day, _num
from trading_intel.prices.realized_vol import log_returns, realized_vol
from trading_intel.timeutils import eastern_now, live_expiry_floor, same_day_years


# ---------------------------------------------------------------------------
# #94 — Carroll's RV/IV box
# ---------------------------------------------------------------------------

def get_rv_iv_box(
    session: Session,
    *,
    days: int = 60,
) -> dict[str, Any]:
    """Carroll-style RV/IV regime box for SPX.

    Returns the latest RV20, ATM IV (30d), VIX, VIX3M (VX30 proxy), VRP
    (VIX − RV20), the IV/RV ratio, and a trailing series for charting.

    VX30 (30-day constant-maturity VIX future) is not banked; VIX3M is used as
    a term-structure proxy until the VX30 collector lands.
    """
    # Latest SPX RV from quotes_daily
    spx_quotes = session.execute(
        select(QuoteDaily.date, QuoteDaily.close, QuoteDaily.rv20, QuoteDaily.rv60)
        .where(QuoteDaily.symbol == "SPX")
        .order_by(QuoteDaily.date.desc())
        .limit(days)
    ).all()

    if not spx_quotes:
        return {"found": False, "reason": "no SPX quotes_daily rows"}

    latest_q = spx_quotes[0]
    rv20 = _num(latest_q.rv20)
    rv60 = _num(latest_q.rv60)

    # Latest VIX data
    vix_rows = session.execute(
        select(VixData.date, VixData.vix, VixData.vix3m, VixData.vix9d, VixData.vrp)
        .order_by(VixData.date.desc())
        .limit(days)
    ).all()

    latest_vix = vix_rows[0] if vix_rows else None
    vix = _num(latest_vix.vix) if latest_vix else None
    vix3m = _num(latest_vix.vix3m) if latest_vix else None
    vix9d = _num(latest_vix.vix9d) if latest_vix else None

    # ATM IV from latest greeks snapshot (30d-ish tenor)
    gs = session.execute(
        select(GreeksSnapshot.atm_iv, GreeksSnapshot.spot, GreeksSnapshot.ts)
        .where(GreeksSnapshot.symbol == "SPX", GreeksSnapshot.source == "convex")
        .order_by(GreeksSnapshot.ts.desc())
    ).first()
    atm_iv = _num(gs.atm_iv) if gs else None

    # 2026-11-13 data check D4: atm_iv is the FRONT expiry on a calendar clock, so as a share of
    # VIX it reads ~0.58 on Fridays vs ~0.84 Tue/Wed (the weekend sits inside the time left).
    # The IV/RV ratio uses the 1-month constant-maturity ATM IV (iv_tenor_snapshots, 30d) instead,
    # falling back to VIX/100; atm_iv stays in the output, labelled.
    iv_1m, iv_1m_src = None, None
    try:
        cm = session.execute(
            select(IvTenorSnapshot.iv_atm, IvTenorSnapshot.ts)
            .where(IvTenorSnapshot.symbol.in_(("SPX", "SPY")), IvTenorSnapshot.tenor_dte == 30,
                   IvTenorSnapshot.iv_atm.is_not(None))
            .order_by(IvTenorSnapshot.ts.desc(), IvTenorSnapshot.symbol)
        ).first()
    except Exception:  # noqa: BLE001 - table missing (slim DB): fall back to VIX below
        session.rollback()
        cm = None
    if cm is not None:
        iv_1m, iv_1m_src = _num(cm.iv_atm), f"1M constant-maturity ATM IV ({cm.ts.isoformat()})"
    elif vix is not None:
        iv_1m, iv_1m_src = vix / 100.0, "VIX / 100 (no 1M constant-maturity row)"

    # Compute derived fields
    vrp = round(vix - rv20 * 100, 2) if vix is not None and rv20 is not None else None
    iv_rv_ratio = round(iv_1m / rv20, 2) if iv_1m is not None and rv20 and rv20 > 0 else None

    # Term structure slope: VIX3M − VIX (positive = contango = calm)
    term_slope = round(vix3m - vix, 2) if vix3m is not None and vix is not None else None

    # Build trailing series for charting
    series: list[dict[str, Any]] = []
    vix_lookup = {r.date: r for r in vix_rows} if vix_rows else {}
    for q in reversed(spx_quotes):
        v = vix_lookup.get(q.date)
        row: dict[str, Any] = {"date": q.date.isoformat()}
        row["rv20"] = _num(q.rv20)
        row["rv60"] = _num(q.rv60)
        if v:
            row["vix"] = _num(v.vix)
            row["vix3m"] = _num(v.vix3m)
        series.append(row)

    return {
        "found": True,
        "as_of": latest_q.date.isoformat(),
        "spot": _num(gs.spot) if gs else None,
        "rv20": _num(rv20) if rv20 is not None else None,
        "rv60": _num(rv60) if rv60 is not None else None,
        "atm_iv": _num(atm_iv) if atm_iv is not None else None,
        "atm_iv_note": "front-expiry IV on a calendar clock (weekday-biased); not used for IV/RV",
        "iv_1m": iv_1m,
        "iv_rv_basis": iv_1m_src,
        "vix": vix,
        "vix9d": vix9d,
        "vix3m_proxy_vx30": vix3m,
        "vrp_pts": vrp,
        "iv_rv_ratio": iv_rv_ratio,
        "term_slope_3m_1m": term_slope,
        "note": "VX30 proxied by VIX3M until VX30 collector is built",
        "series": series,
        "count": len(series),
    }


# ---------------------------------------------------------------------------
# #90 — Spot-vol beta gauge
# ---------------------------------------------------------------------------

def get_spot_vol_beta(
    session: Session,
    *,
    window: int = 21,
    days: int = 60,
) -> dict[str, Any]:
    """Spot-vol beta (SPX vs VIX) over a configurable rolling window.

    β = cov(Δln(SPX), Δln(VIX)) / var(Δln(SPX))

    Negative β is normal (spot down → vol up). More negative = stronger
    reactivity. Today's residual = actual Δln(VIX) − β·Δln(SPX): positive
    residual means vol ran hotter than the beta relationship implied.

    Also returns the pre-banked 60d beta from ``index_skew_daily`` for
    comparison.
    """
    # Fetch SPX close + VIX close, aligned by date
    spx_rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == "SPX")
        .order_by(QuoteDaily.date.desc())
        .limit(days + window + 5)
    ).all()

    vix_rows = session.execute(
        select(VixData.date, VixData.vix)
        .order_by(VixData.date.desc())
        .limit(days + window + 5)
    ).all()

    if len(spx_rows) < window + 2 or len(vix_rows) < window + 2:
        return {"found": False, "reason": "insufficient history for beta calculation"}

    # Build aligned DataFrame
    spx_df = pd.DataFrame([(r.date, float(r.close)) for r in spx_rows], columns=["date", "spx"])
    vix_df = pd.DataFrame(
        [(r.date, float(r.vix)) for r in vix_rows if r.vix is not None],
        columns=["date", "vix"],
    )
    merged = spx_df.merge(vix_df, on="date").sort_values("date").reset_index(drop=True)

    if len(merged) < window + 2:
        return {"found": False, "reason": "insufficient aligned SPX/VIX data"}

    # Log returns
    merged["spx_ret"] = np.log(merged["spx"] / merged["spx"].shift(1))
    merged["vix_ret"] = np.log(merged["vix"] / merged["vix"].shift(1))
    merged = merged.dropna(subset=["spx_ret", "vix_ret"])

    # Rolling beta
    cov = merged["spx_ret"].rolling(window).cov(merged["vix_ret"])
    var = merged["spx_ret"].rolling(window).var()
    merged["beta"] = cov / var

    latest = merged.iloc[-1]
    beta_now = float(latest["beta"]) if pd.notna(latest["beta"]) else None

    # Today's residual
    residual = None
    if beta_now is not None and pd.notna(latest["spx_ret"]) and pd.notna(latest["vix_ret"]):
        predicted_vix_ret = beta_now * float(latest["spx_ret"])
        residual = round(float(latest["vix_ret"]) - predicted_vix_ret, 6)

    # Pre-banked 60d beta from index_skew_daily
    skew_row = session.execute(
        select(IndexSkewDaily.date, IndexSkewDaily.vix_spx_beta_60d)
        .order_by(IndexSkewDaily.date.desc())
    ).first()
    banked_beta_60d = _num(skew_row.vix_spx_beta_60d) if skew_row else None

    # Build trailing series
    valid = merged.dropna(subset=["beta"]).tail(days)
    series = [
        {
            "date": row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"]),
            "beta": round(float(row["beta"]), 4),
        }
        for _, row in valid.iterrows()
    ]

    return {
        "found": True,
        "as_of": latest["date"].isoformat() if hasattr(latest["date"], "isoformat") else str(latest["date"]),
        "window": window,
        "beta": round(beta_now, 4) if beta_now is not None else None,
        "residual_today": residual,
        "banked_beta_60d": round(banked_beta_60d, 4) if banked_beta_60d is not None else None,
        "interpretation": _beta_interpretation(beta_now),
        "series": series,
        "count": len(series),
    }


def _beta_interpretation(beta: float | None) -> str | None:
    if beta is None:
        return None
    if beta < -2.0:
        return "extreme reactivity — vol spikes sharply on dips"
    if beta < -1.0:
        return "elevated reactivity — vol responsive to spot moves"
    if beta < -0.5:
        return "normal — typical spot-vol relationship"
    if beta < 0:
        return "muted — vol barely reacting to spot"
    return "inverted — vol moving WITH spot (unusual)"


# ---------------------------------------------------------------------------
# #95 — Implied-correlation panel
# ---------------------------------------------------------------------------

def get_implied_correlation(
    session: Session,
    *,
    days: int = 60,
) -> dict[str, Any]:
    """Cboe implied-correlation + dispersion panel.

    Reads COR1M, COR3M, VIXEQ, DSPX and their 252d percentiles from
    ``index_skew_daily``. High correlation = index-vol-led (macro) regime;
    low correlation (high dispersion) = single-stock alpha regime.

    ``cor_slope`` = COR1M − COR3M: positive = near-term correlation stress
    (index hedgers bidding short-dated correlation).
    """
    rows = session.execute(
        select(
            IndexSkewDaily.date,
            IndexSkewDaily.cor1m,
            IndexSkewDaily.cor1m_pctile_252d,
            IndexSkewDaily.cor3m,
            IndexSkewDaily.cor3m_pctile_252d,
            IndexSkewDaily.vixeq,
            IndexSkewDaily.vixeq_pctile_252d,
            IndexSkewDaily.dspx,
            IndexSkewDaily.dspx_pctile_252d,
            IndexSkewDaily.vixeq_vix_spread,
        )
        .order_by(IndexSkewDaily.date.desc())
        .limit(days)
    ).all()

    if not rows:
        return {"found": False, "reason": "no index_skew_daily rows"}

    latest = rows[0]
    cor1m = _num(latest.cor1m)
    cor3m = _num(latest.cor3m)
    cor_slope = round(cor1m - cor3m, 2) if cor1m is not None and cor3m is not None else None

    # Build series
    series: list[dict[str, Any]] = []
    for r in reversed(rows):
        series.append({
            "date": r.date.isoformat(),
            "cor1m": _num(r.cor1m),
            "cor3m": _num(r.cor3m),
            "vixeq": _num(r.vixeq),
            "dspx": _num(r.dspx),
        })

    return {
        "found": True,
        "as_of": latest.date.isoformat(),
        "cor1m": cor1m,
        "cor1m_pctile": _num(latest.cor1m_pctile_252d),
        "cor3m": cor3m,
        "cor3m_pctile": _num(latest.cor3m_pctile_252d),
        "cor_slope": cor_slope,
        "vixeq": _num(latest.vixeq),
        "vixeq_pctile": _num(latest.vixeq_pctile_252d),
        "dspx": _num(latest.dspx),
        "dspx_pctile": _num(latest.dspx_pctile_252d),
        "vixeq_vix_spread": _num(latest.vixeq_vix_spread),
        "regime": _correlation_regime(cor1m, _num(latest.cor1m_pctile_252d)),
        "series": series,
        "count": len(series),
    }


def _correlation_regime(cor1m: float | None, pctile: float | None) -> str | None:
    """One-word regime label from 1-month correlation + its percentile."""
    if cor1m is None:
        return None
    if pctile is not None:
        if pctile > 0.85:
            return "extreme correlation — macro-driven, hedging heavy"
        if pctile > 0.65:
            return "elevated correlation — index-level risk dominates"
        if pctile < 0.15:
            return "extreme dispersion — single-stock alpha regime"
        if pctile < 0.35:
            return "low correlation — stock-picker's market"
    return "normal correlation range"


# ---------------------------------------------------------------------------
# #87 — Color at spot (dΓ/dt EOD descriptor for Map tab)
# ---------------------------------------------------------------------------

def get_color_at_spot(
    session: Session,
    symbol: str = "SPX",
    *,
    hours_ahead: float = 6.5,
) -> dict[str, Any]:
    """Color (dΓ/dt) at spot — how dealer gamma changes from time alone.

    Prices the OI chain at the latest book timestamp, then re-prices with
    the clock advanced ``hours_ahead`` (default 6.5 h ≈ open→close). OI,
    spot, IV all frozen — only time moves. The difference is the Color:
    positive = dealers get more long gamma through the day (supportive);
    negative = dealers get more short gamma (destabilising).

    Returns gamma_now, gamma_later, color (delta), and the same values at
    the flip level for context. Units: $bn per 1% move.
    """
    sym = symbol.upper()

    # Latest OI chain timestamp
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == sym, OiChainEod.source.like("convex%"))
        .order_by(OiChainEod.ts.desc())
    ).scalars().first()

    # Latest spot
    spot_row = session.execute(
        select(GreeksSnapshot.spot)
        .where(GreeksSnapshot.symbol == sym, GreeksSnapshot.source == "convex")
        .order_by(GreeksSnapshot.ts.desc())
    ).first()
    spot = float(spot_row[0]) if spot_row and spot_row[0] is not None else None

    if ts is None or spot is None:
        return {"symbol": sym, "found": False, "reason": "no oi_chain_eod or spot data"}

    # Build chain
    book_day = ts.date()
    floor = live_expiry_floor(book_day)
    chain_rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv,
            OiChainEod.oi, OiChainEod.expiry,
        ).where(
            OiChainEod.symbol == sym,
            OiChainEod.source.like("convex%"),
            OiChainEod.ts == ts,
            OiChainEod.expiry >= floor,
            OiChainEod.dte <= 400,
            OiChainEod.iv.isnot(None),
        )
    ).all()

    if not chain_rows:
        return {"symbol": sym, "found": False, "reason": "empty chain"}

    # Same-day expiry handling (matches profile_tool._book_chain)
    d0_end = datetime.combine(book_day, time()) + timedelta(
        seconds=same_day_years(book_day) * 365.0 * 24 * 3600
    )
    chain = pd.DataFrame([
        {
            "strike": r.strike,
            "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
            "iv": r.iv,
            "oi": r.oi,
            "expiration": d0_end if r.expiry == book_day else r.expiry,
        }
        for r in chain_rows
    ])

    # Price gamma NOW (at book timestamp)
    prof_now = greek_profiles(
        chain, spot, span=0.001, n_points=3, ref=book_day, greeks=("gamma",)
    )
    g_now = (prof_now or {}).get("gamma", {})
    gamma_now = (g_now.get("all") or [None, None])[1]  # middle of 3-point grid = at spot

    # Price gamma LATER — advance clock by hours_ahead
    # Shift all expirations backward by hours_ahead (equivalent to advancing clock)
    hours_in_years = hours_ahead / (365.0 * 24.0)
    chain_later = chain.copy()
    # For datetime expirations, subtract the time delta
    for idx in chain_later.index:
        exp = chain_later.at[idx, "expiration"]
        if isinstance(exp, datetime):
            chain_later.at[idx, "expiration"] = exp - timedelta(hours=hours_ahead)
        elif isinstance(exp, date):
            # date-type expiry: convert to datetime then shift
            exp_dt = datetime.combine(exp, time(16, 0))  # 4pm close
            chain_later.at[idx, "expiration"] = exp_dt - timedelta(hours=hours_ahead)

    prof_later = greek_profiles(
        chain_later, spot, span=0.001, n_points=3, ref=book_day, greeks=("gamma",)
    )
    g_later = (prof_later or {}).get("gamma", {})
    gamma_later = (g_later.get("all") or [None, None])[1]

    # Color = gamma_later − gamma_now (how gamma changes as time passes)
    color = None
    if gamma_now is not None and gamma_later is not None:
        color = round(float(gamma_later) - float(gamma_now), 6)

    # Flip levels for context
    flip_now = g_now.get("flip")
    flip_later = g_later.get("flip")

    return {
        "symbol": sym,
        "found": True,
        "as_of": book_day.isoformat(),
        "spot": spot,
        "hours_ahead": hours_ahead,
        "gamma_now_bn": round(float(gamma_now), 4) if gamma_now is not None else None,
        "gamma_later_bn": round(float(gamma_later), 4) if gamma_later is not None else None,
        "color_bn": round(color, 4) if color is not None else None,
        "color_sign": (
            "positive — dealers gain long gamma (supportive)" if color and color > 0
            else "negative — dealers gain short gamma (destabilising)" if color and color < 0
            else "flat" if color == 0
            else None
        ),
        "flip_now": round(flip_now, 1) if flip_now is not None else None,
        "flip_later": round(flip_later, 1) if flip_later is not None else None,
        "units": "$bn per 1% move",
    }
