"""Dashboard readers for stored options-flow snapshots.

Thin ``Session`` queries over ``flow_snapshots`` (written by the flow collector)
returning tidy structures for the Flow page: the latest snapshot per symbol, a
watchlist-wide overview frame, and the JSON top-prints / packages detail.

Regime descriptors only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import FlowSnapshot


def latest_flow_ts(session: Session, symbol: str) -> datetime | None:
    """Timestamp of the newest ``flow_snapshots`` row for ``symbol``."""
    return session.execute(
        select(FlowSnapshot.ts)
        .where(FlowSnapshot.symbol == symbol)
        .order_by(FlowSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_latest_flow(session: Session, symbol: str) -> FlowSnapshot | None:
    """Most recent ``flow_snapshots`` row for ``symbol`` (or None)."""
    return session.execute(
        select(FlowSnapshot)
        .where(FlowSnapshot.symbol == symbol)
        .order_by(FlowSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_watchlist_flow(session: Session, symbols: list[str]) -> pd.DataFrame:
    """Latest aggregate flow per symbol, one row each (newest snapshot).

    Columns: ``symbol, ts, call_notional, put_notional, net_premium,
    put_call_ratio, tilt, n_prints``. Symbols with no stored flow are omitted.
    """
    rows: list[dict] = []
    for symbol in symbols:
        snap = load_latest_flow(session, symbol)
        if snap is None:
            continue
        rows.append(
            {
                "symbol": snap.symbol,
                "ts": snap.ts,
                "call_notional": snap.call_notional,
                "put_notional": snap.put_notional,
                "net_premium": snap.net_premium,
                "put_call_ratio": snap.put_call_ratio,
                "tilt": snap.tilt,
                "n_prints": snap.n_prints,
            }
        )
    return pd.DataFrame(rows)
