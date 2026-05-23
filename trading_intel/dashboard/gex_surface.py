"""Pure data-prep for the GEX-by-strike time-series ("surface") page.

Stacks the per-snapshot net-signed-GEX-by-strike profile (calls +, puts -, the
project's GEX convention — MEMORY Formulas) across stored ``greeks_chain``
snapshots into a strike x time matrix for a Convex-style heatmap, plus a
spot/flip overlay read from ``greeks_snapshots``.

Cadence note: ``chain_snapshot`` runs once daily, so this yields a
daily-resolution series (one column per trading day). Intraday resolution would
need a heavier intraday chain collector — a deliberate follow-up, not this.

Everything here is side-effect-free and unit-testable against in-memory SQLite
(create only ``greeks_chain`` / ``greeks_snapshots``). Per the FlashAlpha rule
(CLAUDE.md rule 4) the GEX surface is a *regime descriptor*, not a signal —
nothing in this module emits an alert.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.dashboard.ticker_data import (
    _chain_rows_to_frame,
    gex_by_strike,
    load_snapshot_history,
)
from trading_intel.memory.models import GreeksChain

_LONG_COLS = ["ts", "strike", "net_gex"]
_OVERLAY_COLS = ["ts", "spot", "gex_flip"]


def _recent_chain_ts(session: Session, symbol: str, *, days: int) -> list[datetime]:
    """Distinct ``greeks_chain`` timestamps for ``symbol`` within ``days``, oldest first.

    Bounds the scan by both a row count (``days`` distinct snapshots, daily
    cadence) and an absolute cutoff so a stale gap can't drag in old columns.
    """
    rows = list(
        session.execute(
            select(GreeksChain.ts)
            .where(GreeksChain.symbol == symbol)
            .distinct()
            .order_by(GreeksChain.ts.desc())
            .limit(days)
        ).scalars()
    )
    if not rows:
        return []
    cutoff = max(rows) - timedelta(days=days)
    return sorted(ts for ts in rows if ts >= cutoff)


def _expiry_within(chain: pd.DataFrame, ts: datetime, expiry_within_days: int) -> pd.DataFrame:
    """Keep only strikes whose expiry is within ``expiry_within_days`` DTE of ``ts``."""
    if chain.empty or "expiry" not in chain.columns:
        return chain
    dte = (pd.to_datetime(chain["expiry"]) - pd.Timestamp(ts)).dt.days
    return chain[(dte >= 0) & (dte <= expiry_within_days)]


def _spot_by_date(session: Session, symbol: str, *, days: int) -> dict[date, float]:
    """Map each snapshot day to its spot, for the spot-relative strike window."""
    hist = load_snapshot_history(session, symbol, days=days)
    if hist.empty:
        return {}
    out: dict[date, float] = {}
    for ts, spot in zip(hist["ts"], hist["spot"], strict=False):
        if spot is not None and not pd.isna(spot):
            out[pd.Timestamp(ts).date()] = float(spot)
    return out


def load_gex_strike_series(
    session: Session,
    symbol: str,
    *,
    days: int = 30,
    expiry_within_days: int | None = None,
    pct_range: float | None = 0.03,
) -> pd.DataFrame:
    """Net signed GEX by strike for each stored chain snapshot in range, oldest first.

    Returns a tidy long frame with columns ``ts``, ``strike``, ``net_gex``
    (calls +, puts -). Optionally restricts each snapshot to strikes expiring
    within ``expiry_within_days`` of that snapshot (a near-term gamma view).
    ``pct_range`` (default 0.03 = +/-3% of that day's spot) trims the chain to a
    near-the-money band so the heatmap focuses on the active strikes instead of
    the full +/-15% pull; pass ``None`` to keep every strike. The spot per day
    comes from ``greeks_snapshots``; days with no stored spot keep all strikes.
    Empty frame when no chain snapshots are stored for ``symbol``.
    """
    ts_list = _recent_chain_ts(session, symbol, days=days)
    if not ts_list:
        return pd.DataFrame(columns=_LONG_COLS)

    rows = list(
        session.execute(
            select(GreeksChain).where(
                GreeksChain.symbol == symbol, GreeksChain.ts.in_(ts_list)
            )
        ).scalars()
    )
    frame = _chain_rows_to_frame(rows)
    if frame.empty:
        return pd.DataFrame(columns=_LONG_COLS)
    frame = frame.assign(ts=[r.ts for r in rows])

    spot_by_date = _spot_by_date(session, symbol, days=days) if pct_range else {}

    parts: list[pd.DataFrame] = []
    for ts in ts_list:
        chain = frame[frame["ts"] == ts]
        if expiry_within_days is not None:
            chain = _expiry_within(chain, ts, expiry_within_days)
        by_strike = gex_by_strike(chain)
        if pct_range:
            spot = spot_by_date.get(pd.Timestamp(ts).date())
            if spot and spot > 0:
                lo, hi = spot * (1.0 - pct_range), spot * (1.0 + pct_range)
                by_strike = by_strike[(by_strike["strike"] >= lo) & (by_strike["strike"] <= hi)]
        if by_strike.empty:
            continue
        parts.append(by_strike.assign(ts=ts).rename(columns={"gex": "net_gex"}))

    if not parts:
        return pd.DataFrame(columns=_LONG_COLS)
    out = pd.concat(parts, ignore_index=True)
    return out[_LONG_COLS].sort_values(["ts", "strike"]).reset_index(drop=True)


def gex_strike_matrix(series: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long GEX series to ``index=strike, columns=ts, values=net_gex``.

    Strikes are reindexed to the sorted union across all snapshots so a strike
    missing from one column shows as NaN (a gap) rather than collapsing rows.
    Empty in -> empty out.
    """
    if series is None or series.empty:
        return pd.DataFrame()
    matrix = series.pivot_table(
        index="strike", columns="ts", values="net_gex", aggfunc="sum"
    )
    return matrix.sort_index().sort_index(axis=1)


def spot_flip_overlay(session: Session, symbol: str, *, days: int = 30) -> pd.DataFrame:
    """``[ts, spot, gex_flip]`` from ``greeks_snapshots`` for the overlay lines.

    Thin projection over ``ticker_data.load_snapshot_history``; oldest first.
    Empty frame when no aggregate snapshots are stored.
    """
    hist = load_snapshot_history(session, symbol, days=days)
    if hist.empty:
        return pd.DataFrame(columns=_OVERLAY_COLS)
    return hist[_OVERLAY_COLS].reset_index(drop=True)
