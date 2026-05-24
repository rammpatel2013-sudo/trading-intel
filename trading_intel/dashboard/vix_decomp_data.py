"""Live VIX-decomposition loader: read two SPX EOD chains -> a decomposition.

Bridges the persisted ``oi_chain_eod`` snapshots to the pure
``greeks/vix_decomposition`` transform. For the two most recent SPX snapshot
days it picks the expiry nearest 30 DTE, derives a spot proxy from the ~0.50
delta strike, scales Convex's decimal IV to vol points, and runs the 6-factor
decomposition. Returns a status object so the dashboard can show "accumulating
history" until two snapshots exist. Descriptive only - FlashAlpha rule 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.errors import ComputationError
from trading_intel.greeks.vix_decomposition import (
    VixDecomposition,
    decompose,
    skew_from_chain,
)
from trading_intel.memory.models import OiChainEod

VIX_TENOR_DTE = 30


@dataclass(frozen=True)
class DecompResult:
    """Outcome of a decomposition attempt + enough context to explain a miss."""

    decomposition: VixDecomposition | None
    days_available: int
    as_of: datetime | None
    prior: datetime | None


def _skew_for_day(session: Session, symbol: str, ts: datetime):
    """Build a SkewSnapshot for ``symbol`` on snapshot ``ts`` (nearest-30d expiry)."""
    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.delta, OiChainEod.dte
        ).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.iv.is_not(None),
            OiChainEod.delta.is_not(None),
        )
    ).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["strike", "cp", "iv", "delta", "dte"])
    df = df.dropna(subset=["strike", "iv", "delta"])
    if df.empty:
        return None

    # Pick the single expiry whose DTE is closest to the VIX's 30-day tenor.
    dtes = df["dte"].dropna().unique()
    if len(dtes) == 0:
        return None
    target = min((int(d) for d in dtes), key=lambda d: abs(d - VIX_TENOR_DTE))
    day = df[df["dte"] == target].copy()

    # Convex stores IV as a decimal; scale to vol points so factors are VIX-comparable.
    if day["iv"].median() < 2.0:
        day["iv"] = day["iv"] * 100.0

    # Spot proxy: the call strike nearest 0.50 delta (ATM-forward).
    calls = day[day["cp"].astype(str).str.upper().str[0] == "C"]
    anchor = calls if not calls.empty else day
    idx = (anchor["delta"].abs() - 0.50).abs().idxmin()
    spot = float(anchor.loc[idx, "strike"])

    try:
        return skew_from_chain(day, spot)
    except ComputationError:
        return None


def latest_spx_decomposition(session: Session, *, symbol: str = "SPX") -> DecompResult:
    """Decompose the most recent SPX day-over-day move, or report why we can't yet."""
    days = list(
        session.execute(
            select(OiChainEod.ts)
            .where(OiChainEod.symbol == symbol)
            .distinct()
            .order_by(OiChainEod.ts.desc())
        ).scalars()
    )
    n = len(days)
    if n < 2:
        return DecompResult(None, n, days[0] if days else None, None)

    now_ts, prev_ts = days[0], days[1]
    now_skew = _skew_for_day(session, symbol, now_ts)
    prev_skew = _skew_for_day(session, symbol, prev_ts)
    if now_skew is None or prev_skew is None:
        return DecompResult(None, n, now_ts, prev_ts)
    try:
        decomp = decompose(prev_skew, now_skew)
    except ComputationError:
        decomp = None
    return DecompResult(decomp, n, now_ts, prev_ts)
