"""Additional MCP tool functions — one granular reader per collected table.

Companion to ``trading_intel.mcp.tools``. Split out to keep each module under
the 400-line cap (CLAUDE.md code style). Same contract as ``tools``: every
function takes a ``Session`` (plus args) and returns a JSON-serialisable dict.

Tools are READ-ONLY by design (FlashAlpha rule 4 + rule 1 spirit). They never
call ConvexValue, never write to ``signals``, and never persist anything —
everything stored here arrived via a scheduled collector job.

Coverage (table -> tool):
    oi_chain_eod     -> get_walls, get_oi_changes
    gex_rolling/term -> get_gex_term
    vol_richness     -> get_vol_richness
    vix_data         -> get_vix
    index_skew_daily -> get_index_skew
    iv_tenor_snapshots-> get_iv_tenor
    vix_options_chain-> get_vix_options
    live_gex         -> get_live_gex
    intraday_flow    -> get_intraday_flow
    delta_flow       -> get_delta_flow
    research_notes   -> get_research_note
    surface_reports  -> get_surface_report
    watchlist_entries-> get_research_watchlist
    signals          -> get_signals
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.dashboard.skew_data import vix_options_today
from trading_intel.dashboard.ticker_data import (
    intraday_by_strike,
    latest_snapshot,
    load_intraday_flow_series,
    load_latest_intraday_flow,
)
from trading_intel.flow.scorecard import build_scorecard
from trading_intel.mcp.tools import _iso_day, _iso_ts, _normalise_symbols, _num
from trading_intel.memory.models import (
    DeltaFlow,
    GexRolling,
    GexTerm,
    IndexSkewDaily,
    IvTenorSnapshot,
    LiveGex,
    OiChainEod,
    ResearchNote,
    Signal,
    SurfaceReport,
    VixData,
    VolRichness,
    WatchlistEntry,
)

log = structlog.get_logger(__name__)

_TOP_DEFAULT = 15


# ── oi_chain_eod ───────────────────────────────────────────────────────


def _latest_oi_ts(session: Session, symbol: str) -> datetime | None:
    return session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == symbol)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_walls(session: Session, symbol: str, *, dte_max: int = 60) -> dict[str, Any]:
    """Call-wall / put-wall for one ticker from the latest EOD per-strike chain.

    The *call wall* is the strike carrying the most call-side gamma-OI (``gxoi``)
    — dealer-defended resistance / a pin; the *put wall* is the analogous put-side
    support. Sums ``gxoi`` by strike within each side over options with
    ``dte <= dte_max`` from the newest ``oi_chain_eod`` snapshot. Regime
    descriptor only — never a signal (FlashAlpha rule 4).
    """
    sym = symbol.strip().upper()
    dte_c = max(0, min(int(dte_max), 365))
    ts = _latest_oi_ts(session, sym)
    if ts is None:
        return {"symbol": sym, "found": False, "call_wall": None, "put_wall": None}

    rows = session.execute(
        select(
            OiChainEod.strike,
            OiChainEod.cp,
            func.sum(OiChainEod.gxoi).label("gxoi"),
            func.sum(OiChainEod.oi).label("oi"),
        )
        .where(
            OiChainEod.symbol == sym,
            OiChainEod.ts == ts,
            OiChainEod.dte <= dte_c,
            OiChainEod.dte >= 0,
        )
        .group_by(OiChainEod.strike, OiChainEod.cp)
    ).all()
    if not rows:
        return {"symbol": sym, "found": False, "call_wall": None, "put_wall": None}

    out: dict[str, Any] = {"symbol": sym, "as_of": _iso_day(ts), "dte_max": dte_c}
    snap = latest_snapshot(session, sym)
    out["spot"] = _num(snap.spot) if snap is not None else None
    for label, code in (("call", "C"), ("put", "P")):
        side = [
            {"strike": _num(r.strike), "gxoi": _num(r.gxoi), "oi": _num(r.oi)}
            for r in rows
            if str(r.cp).upper().startswith(code) and r.gxoi is not None
        ]
        side.sort(key=lambda d: (d["gxoi"] or 0.0), reverse=True)
        out[f"{label}_wall"] = side[0]["strike"] if side else None
        out[f"{label}_wall_gxoi"] = side[0]["gxoi"] if side else None
        out[f"{label}_top_strikes"] = side[:5]
    out["found"] = True
    return out


def get_oi_changes(
    session: Session, symbol: str, *, dte_max: int = 60, top: int = _TOP_DEFAULT
) -> dict[str, Any]:
    """Biggest day-over-day open-interest changes per strike (latest EOD snapshot).

    Reads ``oi_chain_eod`` (vendor ``oi_ch`` -> ``oi_change``). Returns the strikes
    with the largest absolute OI change plus net call / put OI-change totals — a
    descriptive read on where positioning was added or closed. Rule 4: descriptor
    only, never a signal.
    """
    sym = symbol.strip().upper()
    dte_c = max(0, min(int(dte_max), 365))
    top_c = max(1, min(int(top), 100))
    ts = _latest_oi_ts(session, sym)
    if ts is None:
        return {"symbol": sym, "found": False, "rows": [], "count": 0}

    rows = session.execute(
        select(
            OiChainEod.expiry, OiChainEod.strike, OiChainEod.cp, OiChainEod.dte,
            OiChainEod.oi, OiChainEod.oi_change, OiChainEod.volume,
        ).where(
            OiChainEod.symbol == sym,
            OiChainEod.ts == ts,
            OiChainEod.dte <= dte_c,
            OiChainEod.dte >= 0,
        )
    ).all()
    if not rows:
        return {"symbol": sym, "found": False, "rows": [], "count": 0}

    recs = [
        {
            "expiry": _iso_day(r.expiry), "strike": _num(r.strike), "cp": r.cp,
            "dte": r.dte, "oi": _num(r.oi), "oi_change": _num(r.oi_change),
            "volume": _num(r.volume),
        }
        for r in rows
    ]
    call_chg = sum((r.oi_change or 0) for r in rows if str(r.cp).upper().startswith("C"))
    put_chg = sum((r.oi_change or 0) for r in rows if str(r.cp).upper().startswith("P"))
    ranked = sorted(recs, key=lambda d: abs(d["oi_change"] or 0.0), reverse=True)[:top_c]
    return {
        "symbol": sym, "as_of": _iso_day(ts), "dte_max": dte_c,
        "net_call_oi_change": float(call_chg), "net_put_oi_change": float(put_chg),
        "rows": ranked, "count": len(ranked), "found": True,
    }


# ── gex_rolling + gex_term ─────────────────────────────────────────────


def get_gex_term(session: Session, symbol: str) -> dict[str, Any]:
    """Rolling total GEX + per-expiration term structure for one ticker (latest EOD).

    ``gex_rolling`` carries net signed gamma-OI over the rolling window; the
    matching ``gex_term`` rows break that out per expiration so you can see where
    the gamma sits along the curve. Descriptor only (rule 4).
    """
    sym = symbol.strip().upper()
    roll = session.execute(
        select(GexRolling)
        .where(GexRolling.symbol == sym)
        .order_by(GexRolling.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if roll is None:
        return {"symbol": sym, "found": False, "term": [], "count": 0}

    term_rows = session.execute(
        select(GexTerm.expiration, GexTerm.dte, GexTerm.gex)
        .where(
            GexTerm.symbol == sym,
            GexTerm.ts == roll.ts,
            GexTerm.source == roll.source,
        )
        .order_by(GexTerm.dte.asc())
    ).all()
    term = [
        {"expiration": _iso_day(r.expiration), "dte": r.dte, "gex": _num(r.gex)}
        for r in term_rows
    ]
    return {
        "symbol": sym, "as_of": _iso_day(roll.ts), "spot": _num(roll.spot),
        "window_days": roll.window_days, "gex_total": _num(roll.gex_total),
        "n_expirations": roll.n_expirations, "term": term, "count": len(term),
        "found": True,
    }


# ── vol_richness ───────────────────────────────────────────────────────


def get_vol_richness(
    session: Session,
    symbols: list[str] | None = None,
    *,
    settings: Settings,
    horizon_dte: int = 30,
) -> dict[str, Any]:
    """Latest IV-vs-forecast-RV richness scan per symbol at one horizon.

    Reads ``vol_richness``: the variance-risk-premium (``vrp_pts``) standardized
    to the name's own history (``vrp_pctile`` / ``iv_rank``), term/skew context
    and the regime-gated descriptive ``label`` (rich/cheap). Descriptor only —
    rule 4.
    """
    syms = _normalise_symbols(session, symbols, settings)
    hz = int(horizon_dte)
    out_rows: list[dict[str, Any]] = []
    for sym in syms:
        row = session.execute(
            select(VolRichness)
            .where(VolRichness.symbol == sym, VolRichness.horizon_dte == hz)
            .order_by(VolRichness.ts.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            continue
        out_rows.append(
            {
                "symbol": sym, "as_of": _iso_day(row.ts), "horizon_dte": hz,
                "iv_atm": _num(row.iv_atm), "fcst_rv": _num(row.fcst_rv),
                "vrp_pts": _num(row.vrp_pts), "vrp_pctile": _num(row.vrp_pctile),
                "iv_rank": _num(row.iv_rank), "term_slope": _num(row.term_slope),
                "skew_25d": _num(row.skew_25d), "regime_zone": row.regime_zone,
                "richness_score": _num(row.richness_score), "label": row.label,
            }
        )
    return {
        "symbols": syms, "horizon_dte": hz, "rows": out_rows,
        "count": len(out_rows), "found": bool(out_rows),
    }


# ── vix_data ───────────────────────────────────────────────────────────


def get_vix(session: Session, *, days: int = 60) -> dict[str, Any]:
    """VIX complex daily series: VIX/VVIX/MOVE, credit OAS, term structure, VRP.

    Reads ``vix_data``. Returns the recent series (oldest first) plus a summary of
    the latest values and the VEGA zone. Descriptor only (rule 4).
    """
    days_c = max(2, min(int(days), 365))
    rows = session.execute(
        select(VixData).order_by(VixData.date.desc()).limit(days_c)
    ).scalars().all()
    if not rows:
        return {"rows": [], "count": 0, "found": False}
    rows = list(reversed(rows))
    series = [
        {
            "date": _iso_day(r.date), "vix": _num(r.vix), "vvix": _num(r.vvix),
            "move": _num(r.move), "hy_oas": _num(r.hy_oas), "ig_oas": _num(r.ig_oas),
            "vix9d": _num(r.vix9d), "vix3m": _num(r.vix3m), "vix6m": _num(r.vix6m),
            "vrp": _num(r.vrp), "vega_zone": r.vega_zone,
        }
        for r in rows
    ]
    last = rows[-1]
    summary = {
        "vix": _num(last.vix), "vvix": _num(last.vvix),
        "term_9d_3m": (
            _num(last.vix9d) - _num(last.vix3m)
            if last.vix9d is not None and last.vix3m is not None else None
        ),
        "vega_zone": last.vega_zone,
    }
    return {"rows": series, "count": len(series), "summary": summary, "found": True}


# ── index_skew_daily ───────────────────────────────────────────────────


def get_index_skew(session: Session, *, days: int = 60) -> dict[str, Any]:
    """Index-level skew & VIX-decomposition daily series.

    Reads ``index_skew_daily``: Cboe SKEW, Nations SDEX/TDEX/VOLI (+ proxies),
    SPX 25d RR and percentile, the VIX-options tail-hedging composite, and the
    VIX term-structure decomposition descriptors. Descriptor only (rule 4).
    """
    days_c = max(2, min(int(days), 365))
    rows = session.execute(
        select(IndexSkewDaily).order_by(IndexSkewDaily.date.desc()).limit(days_c)
    ).scalars().all()
    if not rows:
        return {"rows": [], "count": 0, "found": False}
    rows = list(reversed(rows))
    series = [
        {
            "date": _iso_day(r.date), "cboe_skew": _num(r.cboe_skew),
            "sdex": _num(r.sdex), "sdex_pctile_252d": _num(r.sdex_pctile_252d),
            "spx_rr_25d_30d": _num(r.spx_rr_25d_30d),
            "spx_rr_pctile_252d": _num(r.spx_rr_pctile_252d),
            "vvix": _num(r.vvix),
            "vix_tail_hedging_score": _num(r.vix_tail_hedging_score),
            "voli": _num(r.voli), "tdex": _num(r.tdex),
            "vix_term_9d_30d": _num(r.vix_term_9d_30d),
            "vix_term_3m_30d": _num(r.vix_term_3m_30d),
            "vvix_vix_ratio": _num(r.vvix_vix_ratio),
        }
        for r in rows
    ]
    return {"rows": series, "count": len(series), "found": True}


# ── iv_tenor_snapshots ─────────────────────────────────────────────────


def get_iv_tenor(
    session: Session,
    *,
    symbols: list[str] | None = None,
    tenor_dte: int | None = None,
    days: int = 90,
) -> dict[str, Any]:
    """Constant-maturity forward IV for index ETFs (QQQ/SPY/SPX).

    Reads ``iv_tenor_snapshots``: ATM IV plus the 15Δ/25Δ call and put wings at
    fixed 30d (1M) / 90d (3M) tenors, interpolated in total-variance space (so the
    series doesn't sawtooth on expiry roll). Adds the derived 25Δ and 15Δ risk
    reversals (``iv_put - iv_call``; positive = the usual equity put-skew bid).

    ``symbols`` filters the roots (default: all stored); ``tenor_dte`` filters to
    one tenor (e.g. 30 or 90). ``rows`` is ordered (symbol, tenor, date asc);
    ``latest`` carries the most recent row per (symbol, tenor). Descriptor only
    (FlashAlpha rule 4) — the index ETFs are excluded from the per-strike chain,
    so this is the only stored skew/term read for them.
    """
    days_c = max(2, min(int(days), 365))
    # Caller-supplied roots only need uppercase + order-preserving dedupe here
    # (the watchlist-default path of _normalise_symbols isn't wanted for this tool).
    syms: list[str] | None = None
    if symbols:
        seen: set[str] = set()
        syms = []
        for s in symbols:
            u = s.strip().upper()
            if u and u not in seen:
                seen.add(u)
                syms.append(u)
    filters = []
    if syms:
        filters.append(IvTenorSnapshot.symbol.in_(syms))
    if tenor_dte is not None:
        filters.append(IvTenorSnapshot.tenor_dte == int(tenor_dte))

    # Window relative to the latest stored row (not wall-clock), so the read is
    # stable regardless of when it's called or whether collection is behind.
    max_ts = session.execute(
        select(func.max(IvTenorSnapshot.ts)).where(*filters)
    ).scalar()
    if max_ts is None:
        return {"rows": [], "count": 0, "latest": [], "found": False}
    cutoff = max_ts - timedelta(days=days_c)

    stmt = (
        select(IvTenorSnapshot)
        .where(IvTenorSnapshot.ts >= cutoff, *filters)
        .order_by(
            IvTenorSnapshot.symbol.asc(),
            IvTenorSnapshot.tenor_dte.asc(),
            IvTenorSnapshot.ts.asc(),
        )
    )
    rows = session.execute(stmt).scalars().all()
    if not rows:
        return {"rows": [], "count": 0, "latest": [], "found": False}

    def _rr(put: float | None, call: float | None) -> float | None:
        return (put - call) if (put is not None and call is not None) else None

    series: list[dict[str, Any]] = []
    for r in rows:
        series.append(
            {
                "symbol": r.symbol,
                "ts": _iso_day(r.ts),
                "tenor_dte": r.tenor_dte,
                "iv_atm": _num(r.iv_atm),
                "iv_call_25d": _num(r.iv_call_25d),
                "iv_put_25d": _num(r.iv_put_25d),
                "iv_call_15d": _num(r.iv_call_15d),
                "iv_put_15d": _num(r.iv_put_15d),
                "rr_25d": _num(_rr(r.iv_put_25d, r.iv_call_25d)),
                "rr_15d": _num(_rr(r.iv_put_15d, r.iv_call_15d)),
                "spot": _num(r.spot),
                "n_expiries": r.n_expiries,
            }
        )

    # Most recent row per (symbol, tenor) — series is date-ascending, so the last
    # write per key wins.
    latest: dict[str, dict[str, Any]] = {}
    for row in series:
        latest[f"{row['symbol']}:{row['tenor_dte']}"] = row

    return {
        "rows": series,
        "count": len(series),
        "latest": list(latest.values()),
        "found": True,
    }


# ── vix_options_chain ──────────────────────────────────────────────────


def get_vix_options(session: Session) -> dict[str, Any]:
    """Latest stored EOD VIX-options chain (per expiry/strike/kind).

    Reads ``vix_options_chain`` via the dashboard reader. Returns the raw rows
    plus the call-side OI share — a read on VIX upside (tail-hedge) demand.
    Descriptor only (rule 4).
    """
    df = vix_options_today(session)
    if df is None or df.empty:
        return {"rows": [], "count": 0, "found": False}
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        rows.append(
            {
                "expiration": _iso_day(r.get("expiration")),
                "strike": _num(r.get("strike")), "kind": r.get("opt_kind"),
                "delta": _num(r.get("delta")), "iv": _num(r.get("iv")),
                "oi": _num(r.get("oi")), "volume": _num(r.get("volume")),
            }
        )
    kind = df["opt_kind"].astype(str).str.lower()
    oi = pd.to_numeric(df["oi"], errors="coerce").fillna(0.0)
    total = float(oi.sum())
    call_oi = float(oi[kind.str.startswith("c")].sum())
    return {
        "rows": rows, "count": len(rows),
        "call_oi_share": (call_oi / total) if total > 0 else None,
        "found": True,
    }


# ── live_gex ───────────────────────────────────────────────────────────


def get_live_gex(session: Session, symbol: str) -> dict[str, Any]:
    """Latest intraday per-strike net GEX profile for one ticker.

    Reads ``live_gex`` (near-the-money delta band, refreshed every few minutes in
    RTH, pruned at EOD). Sums signed gamma-OI per strike (calls +, puts -) for the
    newest snapshot — the live gamma map. Descriptor only (rule 4).
    """
    sym = symbol.strip().upper()
    ts = session.execute(
        select(LiveGex.ts)
        .where(LiveGex.symbol == sym)
        .order_by(LiveGex.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return {"symbol": sym, "found": False, "by_strike": [], "count": 0}

    rows = session.execute(
        select(LiveGex.strike, LiveGex.cp, LiveGex.gxoi, LiveGex.spot)
        .where(LiveGex.symbol == sym, LiveGex.ts == ts)
    ).all()
    spot = next((_num(r.spot) for r in rows if r.spot is not None), None)
    by_strike: dict[float, float] = {}
    for r in rows:
        if r.strike is None or r.gxoi is None:
            continue
        sign = 1.0 if str(r.cp).upper().startswith("C") else -1.0
        by_strike[float(r.strike)] = by_strike.get(float(r.strike), 0.0) + sign * float(r.gxoi)
    profile = [
        {"strike": k, "net_gex": v} for k, v in sorted(by_strike.items())
    ]
    net_total = sum(v for _, v in by_strike.items())
    return {
        "symbol": sym, "as_of": _iso_ts(ts), "spot": spot,
        "net_gex_total": net_total, "by_strike": profile,
        "count": len(profile), "found": True,
    }


# ── intraday_flow (0DTE/1DTE) ──────────────────────────────────────────


def get_intraday_flow(session: Session, symbol: str) -> dict[str, Any]:
    """Intraday 0DTE/1DTE volume-weighted exposure build for one ticker.

    Reads ``intraday_flow``: the aggregate cumulative gamma/delta/vanna/charm-vol
    time series (latest session) plus the latest per-strike bars. Focused names
    only (SPX/SPY/QQQ). Descriptor only (rule 4).
    """
    sym = symbol.strip().upper()
    ts, frame = load_latest_intraday_flow(session, sym)
    if ts is None:
        return {"symbol": sym, "found": False, "by_strike": [], "series": []}

    day = pd.Timestamp(ts).date()
    series_df = load_intraday_flow_series(session, sym, day=day)
    series = []
    for _, r in series_df.iterrows():
        series.append(
            {
                "ts": _iso_ts(r.get("ts")), "spot": _num(r.get("spot")),
                "gamma_vol": _num(r.get("gamma_vol")),
                "delta_vol": _num(r.get("delta_vol")),
                "vanna_vol": _num(r.get("vanna_vol")),
                "charm_vol": _num(r.get("charm_vol")),
            }
        )
    bs_df = intraday_by_strike(frame)
    by_strike = []
    for _, r in bs_df.iterrows():
        by_strike.append(
            {
                "strike": _num(r.get("strike")),
                "gamma_vol": _num(r.get("gamma_vol")),
                "delta_vol": _num(r.get("delta_vol")),
                "volume": _num(r.get("volume")),
            }
        )
    return {
        "symbol": sym, "as_of": _iso_ts(ts), "series": series,
        "by_strike": by_strike, "count": len(by_strike), "found": True,
    }


# ── delta_flow ─────────────────────────────────────────────────────────


def get_delta_flow(session: Session, symbol: str, *, days: int = 5) -> dict[str, Any]:
    """Intraday cumulative call/put delta-notional series for one ticker.

    Reads ``delta_flow``: 5-min snapshots of the running dollar-delta of the day's
    option flow, split call vs put and ALL expiries vs the NEXT expiry. Descriptor
    only (rule 4).
    """
    sym = symbol.strip().upper()
    days_c = max(1, min(int(days), 60))
    # Anchor the window to the latest stored row, not wall-clock, so the most
    # recent session is always returned even if collection paused (e.g. weekend).
    latest = session.execute(
        select(func.max(DeltaFlow.ts)).where(DeltaFlow.symbol == sym)
    ).scalar_one_or_none()
    if latest is None:
        return {"symbol": sym, "found": False, "rows": [], "count": 0}
    cutoff = latest - timedelta(days=days_c)
    rows = session.execute(
        select(DeltaFlow)
        .where(DeltaFlow.symbol == sym, DeltaFlow.ts >= cutoff)
        .order_by(DeltaFlow.ts.asc())
    ).scalars().all()
    if not rows:
        return {"symbol": sym, "found": False, "rows": [], "count": 0}
    series = [
        {
            "ts": _iso_ts(r.ts), "spot": _num(r.spot),
            "next_expiry": _iso_day(r.next_expiry),
            "call_notional_all": _num(r.call_notional_all),
            "put_notional_all": _num(r.put_notional_all),
            "call_notional_next": _num(r.call_notional_next),
            "put_notional_next": _num(r.put_notional_next),
        }
        for r in rows
    ]
    last = rows[-1]
    net_all = None
    if last.call_notional_all is not None and last.put_notional_all is not None:
        net_all = float(last.call_notional_all) - float(last.put_notional_all)
    summary = {"as_of": _iso_ts(last.ts), "net_notional_all": net_all}
    return {
        "symbol": sym, "rows": series, "count": len(series),
        "summary": summary, "found": True,
    }


# ── research_notes / surface_reports ───────────────────────────────────


def get_research_note(session: Session, symbol: str) -> dict[str, Any]:
    """Latest narrative research note for one ticker (``research_notes``)."""
    sym = symbol.strip().upper()
    row = session.execute(
        select(ResearchNote)
        .where(ResearchNote.symbol == sym)
        .order_by(ResearchNote.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {"symbol": sym, "found": False, "note_md": None}
    return {
        "symbol": sym, "as_of": _iso_day(row.as_of), "note_md": row.note_md,
        "sources": row.sources, "model": row.model, "found": True,
    }


def get_surface_report(session: Session, symbol: str) -> dict[str, Any]:
    """Latest interpretive vol-surface + flow report for one ticker (``surface_reports``)."""
    sym = symbol.strip().upper()
    row = session.execute(
        select(SurfaceReport)
        .where(SurfaceReport.symbol == sym)
        .order_by(SurfaceReport.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {"symbol": sym, "found": False, "report_md": None}
    return {
        "symbol": sym, "as_of": _iso_day(row.as_of), "report_md": row.report_md,
        "flow_source": row.flow_source, "model": row.model, "found": True,
    }


# ── watchlist_entries ──────────────────────────────────────────────────


def get_research_watchlist(
    session: Session, *, active_only: bool = True, limit: int = 200
) -> dict[str, Any]:
    """Research-driven watchlist: tickers surfaced from uploaded reports.

    Reads ``watchlist_entries`` (LLM-extracted symbol + rationale + sentiment +
    themes from ingested research). Descriptive context only — never a signal
    (rule 4).
    """
    limit_c = max(1, min(int(limit), 1000))
    stmt = select(WatchlistEntry)
    if active_only:
        stmt = stmt.where(WatchlistEntry.active.is_(True))
    rows = session.execute(
        stmt.order_by(WatchlistEntry.added_at.desc()).limit(limit_c)
    ).scalars().all()
    recs = [
        {
            "symbol": r.symbol, "rationale": r.rationale,
            "sentiment": _num(r.sentiment), "confidence": _num(r.confidence),
            "themes": r.themes, "added_at": _iso_ts(r.added_at), "active": r.active,
        }
        for r in rows
    ]
    return {"rows": recs, "count": len(recs), "found": bool(recs)}


# ── tas_daily_flow (accumulation / distribution scorecard) ─────────────


def get_flow_scorecard(
    session: Session,
    *,
    lookback_days: int = 20,
    min_notional: float = 1_000_000.0,
    min_days: int = 2,
    limit: int = 40,
) -> dict[str, Any]:
    """Multi-day accumulation/distribution scorecard from ``tas_daily_flow``.

    Scores each name over ``lookback_days`` of the durable option-tape roll-up:
    ``accum_score`` in [-100, +100] (positive = persistent net buying =
    accumulation; negative = net selling = distribution), with the supporting
    ratios. Descriptive ranking only — never a signal (rule 4). Empty until the
    ``tas_daily_rollup`` job has populated the roll-up table.
    """
    days_c = max(1, min(int(lookback_days), 365))
    limit_c = max(1, min(int(limit), 500))
    board = build_scorecard(
        session, lookback_days=days_c, min_notional=float(min_notional),
        min_days=max(1, int(min_days)),
    )
    if board.empty:
        return {"rows": [], "count": 0, "lookback_days": days_c, "found": False}

    recs = [
        {
            "root": r["root"],
            "accum_score": _num(r["accum_score"]),
            "label": r["label"],
            "days_observed": int(r["days_observed"]),
            "days_net_buy": int(r["days_net_buy"]),
            "days_net_sell": int(r["days_net_sell"]),
            "total_notional": _num(r["total_notional"]),
            "net_dollar_delta": _num(r["net_dollar_delta"]),
            "buy_tilt": _num(r["buy_tilt"]),
            "persistence": _num(r["persistence"]),
        }
        for r in board.head(limit_c).to_dict("records")
    ]
    n_accum = int((board["label"] == "accumulation").sum())
    n_distrib = int((board["label"] == "distribution").sum())
    return {
        "rows": recs,
        "count": len(recs),
        "lookback_days": days_c,
        "n_accumulation": n_accum,
        "n_distribution": n_distrib,
        "found": True,
    }


# ── signals ────────────────────────────────────────────────────────────


def get_signals(
    session: Session, symbol: str | None = None, *, days: int = 30, limit: int = 100
) -> dict[str, Any]:
    """Recent rows from the ``signals`` table (read-only).

    The only validated alerts in the system (written exclusively by
    ``strategies/*`` per rule 4). This tool just reads them back — it never
    writes. ``symbol`` filters to one root; ``days`` bounds the lookback.
    """
    days_c = max(1, min(int(days), 365))
    limit_c = max(1, min(int(limit), 500))
    sym = symbol.strip().upper() if symbol else None
    # Anchor to the latest stored signal so the window doesn't depend on wall-clock.
    max_stmt = select(func.max(Signal.ts))
    if sym:
        max_stmt = max_stmt.where(Signal.symbol == sym)
    latest = session.execute(max_stmt).scalar_one_or_none()
    if latest is None:
        return {"symbol": sym, "rows": [], "count": 0, "found": False}
    cutoff = latest - timedelta(days=days_c)
    stmt = select(Signal).where(Signal.ts >= cutoff)
    if sym:
        stmt = stmt.where(Signal.symbol == sym)
    rows = session.execute(
        stmt.order_by(Signal.ts.desc()).limit(limit_c)
    ).scalars().all()
    recs = [
        {
            "ts": _iso_ts(r.ts), "symbol": r.symbol, "signal_type": r.signal_type,
            "confidence": _num(r.confidence), "payload": r.payload,
        }
        for r in rows
    ]
    return {
        "symbol": sym,
        "rows": recs,
        "count": len(recs),
        "found": bool(recs),
    }
