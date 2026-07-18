"""MCP reader tools for the EM-break / gamma-burn-off system (McGraw pattern).

One granular reader per new descriptor — same contract as ``mcp/extra_tools``:
each takes a ``Session`` (+ args) and returns a JSON-serialisable dict. READ-ONLY
(FlashAlpha rule 4 + rule 1): they read banked tables and compute with the pure
descriptor modules; they never call ConvexValue and never write anything.

    earnings_events        -> get_earnings_calendar
    pre_earnings_straddle  -> get_em_break            (+ quotes_daily)
    gex_rolling / gex_term -> get_gamma_burnoff
    quotes_daily (index)   -> get_vol_control_flow    (RV roll-off -> flow)
    (composite index-level)-> get_systematic_flow

See ``docs/em-break-system-plan.md`` and
``docs/learning/em-break-gamma-burnoff-digest.md``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.earnings.em_break import em_break, over_realization
from trading_intel.flows import aggregate_systematic_buying, cohort_flow, cohort_for
from trading_intel.greeks.gamma_burnoff import burnoff_state, front_dte_share
from trading_intel.memory.models import (
    EarningsEvent,
    GexRolling,
    GexTerm,
    PreEarningsStraddle,
    QuoteDaily,
)
from trading_intel.prices.realized_vol import rv_rolloff_projection
from trading_intel.timeutils import eastern_now


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def _iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d is not None else None


# ── earnings_events -> get_earnings_calendar ───────────────────────────


def get_earnings_calendar(
    session: Session, symbol: str | None = None, *, days: int = 30
) -> dict[str, Any]:
    """Upcoming earnings dates from the banked ``earnings_events`` table.

    Populated by ``scheduler/jobs/earnings_calendar.py`` (ConvexValue ``earn_cal``).
    Filters to ``symbol`` when given, otherwise every name with an event in the
    next ``days``. Descriptor read only (rule 4).
    """
    sym = symbol.strip().upper() if symbol else None
    today = eastern_now().date()
    horizon = today + timedelta(days=max(1, int(days)))
    q = select(EarningsEvent.symbol, EarningsEvent.date, EarningsEvent.time).where(
        EarningsEvent.date >= today, EarningsEvent.date <= horizon
    )
    if sym:
        q = q.where(EarningsEvent.symbol == sym)
    rows = session.execute(q.order_by(EarningsEvent.date.asc(), EarningsEvent.symbol.asc())).all()
    return {
        "symbol": sym,
        "days": int(days),
        "count": len(rows),
        "events": [
            {"symbol": r.symbol, "date": _iso(r.date), "session": r.time} for r in rows
        ],
        "found": bool(rows),
    }


# ── pre_earnings_straddle + quotes_daily -> get_em_break ───────────────


def get_em_break(session: Session, symbol: str) -> dict[str, Any]:
    """How far the last earnings gap broke the pre-earnings expected move.

    Compares the banked pre-earnings straddle baseline (``pre_earnings_straddle``)
    to the realized gap + post-earnings path (``quotes_daily``) via the pure
    ``earnings.em_break`` transforms. Descriptor read only (rule 4).
    """
    sym = symbol.strip().upper()
    pre = session.execute(
        select(PreEarningsStraddle)
        .where(PreEarningsStraddle.symbol == sym)
        .order_by(PreEarningsStraddle.earnings_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if pre is None or not pre.em_pct:
        return {"symbol": sym, "found": False, "reason": "no pre-earnings straddle baseline"}

    edate = pre.earnings_date
    closes = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == sym)
        .order_by(QuoteDaily.date.asc())
    ).all()
    pre_rows = [(d, c) for d, c in closes if d <= edate and c]
    post_rows = [(d, c) for d, c in closes if d > edate and c]
    if not pre_rows or not post_rows:
        return {"symbol": sym, "found": False, "reason": "no pre/post earnings quotes yet"}

    pre_close = float(pre_rows[-1][1])
    first_post = float(post_rows[0][1])
    latest = float(post_rows[-1][1])
    gap_pct = first_post / pre_close - 1.0
    brk = em_break(float(pre.em_pct), gap_pct)
    cum = [float(c) / pre_close - 1.0 for _, c in post_rows]
    ov = over_realization(cum, float(pre.em_pct), gap_pct)
    return {
        "symbol": sym,
        "earnings_date": _iso(edate),
        "em_pct": _num(pre.em_pct),
        "pre_close": pre_close,
        "first_post_close": first_post,
        "latest_close": latest,
        "sessions_since": len(post_rows),
        "em_break": brk,
        "over_realization": ov,
        "found": True,
    }


# ── gex_rolling / gex_term -> get_gamma_burnoff ────────────────────────


def _term_pairs(session: Session, sym: str, ts: datetime, source: str) -> list[tuple]:
    rows = session.execute(
        select(GexTerm.dte, GexTerm.gex).where(
            GexTerm.symbol == sym, GexTerm.ts == ts, GexTerm.source == source
        )
    ).all()
    return [(r.dte, r.gex) for r in rows if r.dte is not None and r.gex is not None]


def get_gamma_burnoff(session: Session, symbol: str) -> dict[str, Any]:
    """Front-expiry gamma share, decay, phase and OPEX countdown.

    Reads the latest ``gex_rolling`` + per-expiration ``gex_term`` and the prior
    snapshot for the front-share decay, then runs the pure
    ``greeks.gamma_burnoff`` reads. Phase uses the front-share proxy (the
    spot-ladder gamma_profile refinement can be layered later). Rule 4.
    """
    sym = symbol.strip().upper()
    roll = session.execute(
        select(GexRolling).where(GexRolling.symbol == sym).order_by(GexRolling.ts.desc()).limit(1)
    ).scalar_one_or_none()
    if roll is None:
        return {"symbol": sym, "found": False, "reason": "no gex_rolling snapshot"}

    term = _term_pairs(session, sym, roll.ts, roll.source)
    front_dte = min((float(d) for d, _ in term), default=None)

    prev_roll = session.execute(
        select(GexRolling)
        .where(GexRolling.symbol == sym, GexRolling.ts < roll.ts)
        .order_by(GexRolling.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    prev_share = None
    if prev_roll is not None:
        prev_term = _term_pairs(session, sym, prev_roll.ts, prev_roll.source)
        if prev_term:
            prev_share = front_dte_share(prev_term)["front_share"]

    state = burnoff_state(
        term, dte_to_front_opex=front_dte, prev_front_share=prev_share
    )
    return {
        "symbol": sym,
        "as_of": _iso(roll.ts),
        "spot": _num(roll.spot),
        **state,
        "term": [{"dte": _num(d), "gex": _num(g)} for d, g in sorted(term, key=lambda p: p[0])],
        "found": True,
    }


# ── quotes_daily (index) -> get_vol_control_flow / get_systematic_flow ──


def _index_closes(session: Session, sym: str, *, limit: int = 260) -> pd.Series:
    rows = session.execute(
        select(QuoteDaily.close)
        .where(QuoteDaily.symbol == sym)
        .order_by(QuoteDaily.date.desc())
        .limit(limit)
    ).all()
    vals = [float(r[0]) for r in reversed(rows) if r[0] is not None]
    return pd.Series(vals, dtype="float64")


def _flow_for_index(
    session: Session, idx: str, settings: Settings, *, window: int, horizon: int
) -> dict[str, Any] | None:
    close = _index_closes(session, idx)
    if close.size < window + 2:
        return None
    proj = rv_rolloff_projection(close, window=window, horizon=horizon)
    rv_path = [float(x) for x in proj["projected_rv"].tolist()]
    rv_today = rv_path[0]
    trend_sign = 1.0 if float(close.iloc[-1]) >= float(close.tail(min(50, close.size)).mean()) else -1.0
    aum = {
        "vol_control": settings.VOL_CONTROL_AUM,
        "cta": settings.CTA_AUM,
        "risk_parity": settings.RISK_PARITY_AUM,
    }
    estimates = []
    for name in ("vol_control", "cta", "risk_parity"):
        coh = cohort_for(name)
        if coh is None:
            continue
        estimates.append(cohort_flow(coh, rv_today, rv_path, aum_usd=aum[name], trend_sign=trend_sign))
    agg = aggregate_systematic_buying(estimates)
    return {
        "index": idx,
        "rv_today": rv_today,
        "rv_terminal": rv_path[-1],
        "trend_sign": trend_sign,
        "by_cohort": {e.cohort: e.buying_usd for e in estimates},
        "vol_control": next(
            (
                {
                    "w_today": e.w_today,
                    "w_terminal": e.w_terminal,
                    "d_exposure": e.d_exposure,
                    "buying_usd": e.buying_usd,
                    "convexity": e.convexity,
                }
                for e in estimates
                if e.cohort == "vol_control"
            ),
            None,
        ),
        "total_buying_usd": agg["total_buying_usd"],
        "direction": agg["direction"],
        "roll_off": [
            {"offset": int(r.session_offset), "projected_rv": _num(r.projected_rv)}
            for r in proj.itertuples()
        ],
    }


def get_vol_control_flow(
    session: Session, index: str = "SPY", *, window: int = 21, horizon: int | None = None
) -> dict[str, Any]:
    """Vol-control (target-vol) buying pressure for an index from the RV roll-off.

    Projects trailing realized vol forward (``rv_rolloff_projection``), maps the
    Δexposure to a $ figure via the cohort assumptions (``flows``). The $ is
    ORDER-OF-MAGNITUDE (assumed AUM/target — see ``flows/registry.py``); read the
    sign + convexity, rank the magnitude. Rule 4.
    """
    settings = get_settings()
    idx = index.strip().upper()
    hz = int(horizon if horizon is not None else settings.RV_ROLLOFF_HORIZON)
    out = _flow_for_index(session, idx, settings, window=int(window), horizon=hz)
    if out is None:
        return {"index": idx, "found": False, "reason": "insufficient quotes_daily history"}
    out["found"] = True
    out["caveat"] = "AUM/target are estimates; consume $ as a percentile, not a hard number"
    return out


def get_systematic_flow(
    session: Session, index: str | None = None, *, window: int = 21
) -> dict[str, Any]:
    """Aggregate systematic (vol-control + CTA + risk-parity) buying across indices.

    Index-level tailwind read for the post-earnings re-entry. Defaults to the
    configured ``VOL_CONTROL_INDEX`` roots (falls back to SPY/QQQ). Rule 4.
    """
    settings = get_settings()
    if index:
        idxs = [index.strip().upper()]
    else:
        idxs = settings.vol_control_index_symbols or ["SPY", "QQQ"]
    hz = int(settings.RV_ROLLOFF_HORIZON)
    per_index = {}
    total = 0.0
    for idx in idxs:
        res = _flow_for_index(session, idx, settings, window=int(window), horizon=hz)
        if res is None:
            continue
        per_index[idx] = res
        total += float(res["total_buying_usd"])
    return {
        "indices": list(per_index.keys()),
        "by_index": per_index,
        "total_buying_usd": total,
        "direction": "buying" if total > 0 else "selling" if total < 0 else "flat",
        "caveat": "AUM/target are estimates; rank the magnitude, trust the sign",
        "found": bool(per_index),
    }
