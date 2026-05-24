"""Live vol-lab loaders: SPX oi_chain_eod snapshots -> IV-surface inputs.

Maps the stored ``oi_chain_eod`` columns to the normalized schema the
``greeks.surface`` / ``greeks.surface_changes`` transforms expect (``expiration``
as datetime, ``opt_kind``, ``strike``, ``delta``, decimal ``iv``) and derives a
spot proxy. Exposes the latest snapshot (surface / smile / term structure) and
the latest two (sticky-strike day-over-day changes). Descriptive only -
FlashAlpha rule 4.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import OiChainEod


def _snapshot_days(session: Session, symbol: str) -> list[datetime]:
    return list(
        session.execute(
            select(OiChainEod.ts)
            .where(OiChainEod.symbol == symbol)
            .distinct()
            .order_by(OiChainEod.ts.desc())
        ).scalars()
    )


def _chain_for_ts(session: Session, symbol: str, ts: datetime) -> pd.DataFrame | None:
    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.delta, OiChainEod.expiry
        ).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.iv.is_not(None),
        )
    ).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["strike", "cp", "iv", "delta", "expiry"])
    df = df.dropna(subset=["strike", "iv", "expiry"])
    if df.empty:
        return None
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def _spot_proxy(df: pd.DataFrame) -> float | None:
    calls = df[df["cp"].astype(str).str.upper().str[0] == "C"].dropna(subset=["delta"])
    anchor = calls if not calls.empty else df.dropna(subset=["delta"])
    if anchor.empty:
        return None
    idx = (anchor["delta"].abs() - 0.50).abs().idxmin()
    return float(anchor.loc[idx, "strike"])


def latest_spx_chain(
    session: Session, *, symbol: str = "SPX"
) -> tuple[pd.DataFrame, float, datetime] | None:
    """``(chain_df, spot, ts)`` for the most recent snapshot, or ``None``."""
    days = _snapshot_days(session, symbol)
    if not days:
        return None
    ts = days[0]
    df = _chain_for_ts(session, symbol, ts)
    if df is None:
        return None
    spot = _spot_proxy(df)
    if spot is None:
        return None
    return df, spot, ts


def prev_curr_spx_chains(
    session: Session, *, symbol: str = "SPX"
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """``(prev_df, curr_df)`` for the two most recent snapshots, or ``None`` if < 2."""
    days = _snapshot_days(session, symbol)
    if len(days) < 2:
        return None
    curr = _chain_for_ts(session, symbol, days[0])
    prev = _chain_for_ts(session, symbol, days[1])
    if curr is None or prev is None:
        return None
    return prev, curr


def list_symbols(session: Session) -> list[str]:
    """Distinct symbols present in ``oi_chain_eod`` (alphabetical)."""
    return list(
        session.execute(
            select(OiChainEod.symbol).distinct().order_by(OiChainEod.symbol)
        ).scalars()
    )


def snapshot_dates(session: Session, symbol: str) -> list[datetime]:
    """Distinct snapshot timestamps for ``symbol``, newest first."""
    return _snapshot_days(session, symbol)


def chain_for_date(
    session: Session, symbol: str, ts: datetime
) -> tuple[pd.DataFrame, float] | None:
    """``(chain_df, spot)`` for ``symbol`` on a specific snapshot ``ts``, or None."""
    df = _chain_for_ts(session, symbol, ts)
    if df is None:
        return None
    spot = _spot_proxy(df)
    if spot is None:
        return None
    return df, spot
