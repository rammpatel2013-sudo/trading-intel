"""Pure data-prep for the Delta-Flow dashboard page.

Loads the day's ``delta_flow`` snapshots for one symbol into a tidy time series:
price (spot) plus the cumulative call/put delta-notional for all expiries and the
next expiry. The chart overlays price (left axis) on the four delta-notional lines
(right axis, in dollars).

Side-effect-free and unit-testable on in-memory SQLite (create only the
``delta_flow`` table). Descriptive flow read-through only — FlashAlpha rule 4.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import DeltaFlow

_COLS = [
    "ts", "spot", "call_notional_all", "put_notional_all",
    "call_notional_next", "put_notional_next",
]


def load_delta_flow_day(
    session: Session, symbol: str, *, day: date | None = None
) -> pd.DataFrame:
    """Delta-flow time series for ``symbol`` on a single session (oldest first).

    ``day`` defaults to the most recent stored session for the symbol. Returns an
    empty, correctly-typed frame when nothing is stored.
    """
    if day is None:
        latest = session.execute(
            select(func.max(DeltaFlow.ts)).where(DeltaFlow.symbol == symbol)
        ).scalar_one_or_none()
        if latest is None:
            return pd.DataFrame(columns=_COLS)
        day = latest.date()

    rows = session.execute(
        select(DeltaFlow)
        .where(DeltaFlow.symbol == symbol)
        .order_by(DeltaFlow.ts.asc())
    ).scalars().all()
    records = [
        {
            "ts": r.ts, "spot": r.spot,
            "call_notional_all": r.call_notional_all,
            "put_notional_all": r.put_notional_all,
            "call_notional_next": r.call_notional_next,
            "put_notional_next": r.put_notional_next,
        }
        for r in rows
        if r.ts is not None and r.ts.date() == day
    ]
    if not records:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(records)


def delta_flow_symbols(session: Session) -> list[str]:
    """Distinct symbols with stored delta-flow data."""
    rows = session.execute(
        select(DeltaFlow.symbol).group_by(DeltaFlow.symbol).order_by(DeltaFlow.symbol)
    ).scalars()
    return list(rows)
