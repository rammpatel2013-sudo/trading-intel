"""MCP tool logic — flow-intelligence drill-in for one name (the Power-BI-style view).

Reads the option tape for a name/day and returns the net-premium 4-way
(call-buy / put-sell / put-buy / call-sell), the institutional-vs-retail split by
print size, the DTE-bucket premium, the accumulation summary, and the most-active
strikes — all from data we already bank (`tas_prints` + `tas_daily_flow` +
`tas_daily_contract`), no new vendor. buy/sell is the tape aggressor side, so it
answers the "is it a buyer?" question rather than guessing from ΔOI.

Kept out of ``server.py`` so the (small) composition-root file stays untouched; the
registration is a one-block ``@mcp.tool()`` wrapper (see the deploy note). Pure
aggregation + payload live in ``trading_intel.flow.intelligence``; this module is
just the DB read. Descriptive only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.flow.intelligence import build_flow_payload
from trading_intel.memory.models import TasDailyContract, TasDailyFlow, TasPrint

_PRINT_COLS = (TasPrint.cp, TasPrint.side, TasPrint.notional, TasPrint.size, TasPrint.expiry)
_DAILY_COLS = (
    "dominant_side",
    "pct_buy",
    "net_dollar_delta",
    "net_premium_call_put",
    "call_notional",
    "put_notional",
    "buy_notional",
    "sell_notional",
    "prints",
)
_CONTRACT_COLS = (
    "expiry",
    "strike",
    "cp",
    "n_prints",
    "total_notional",
    "buy_notional",
    "sell_notional",
    "net_dollar_delta",
    "dominant_side",
)


def _resolve_date(session: Session, root: str, trade_date: date | None) -> date | None:
    if trade_date is not None:
        return trade_date
    return session.execute(
        select(func.max(TasPrint.trade_date)).where(TasPrint.root == root)
    ).scalar()


def flow_intelligence(
    session: Session, symbol: str, *, trade_date: date | None = None, top: int = 8
) -> dict[str, Any]:
    """Flow-intelligence payload for ``symbol`` on ``trade_date`` (latest tape day if None)."""
    root = symbol.upper()
    day = _resolve_date(session, root, trade_date)
    if day is None:
        return {"symbol": root, "error": "no option-tape data for this name"}

    rows = session.execute(
        select(*_PRINT_COLS).where(TasPrint.root == root, TasPrint.trade_date == day)
    ).all()
    prints = pd.DataFrame(rows, columns=["cp", "side", "notional", "size", "expiry"])

    daily_row = session.execute(
        select(TasDailyFlow).where(TasDailyFlow.root == root, TasDailyFlow.trade_date == day)
    ).scalar_one_or_none()
    daily = {c: getattr(daily_row, c) for c in _DAILY_COLS} if daily_row else None

    contract_rows = (
        session.execute(
            select(TasDailyContract)
            .where(TasDailyContract.root == root, TasDailyContract.trade_date == day)
            .order_by(TasDailyContract.total_notional.desc())
            .limit(top)
        )
        .scalars()
        .all()
    )
    contracts = [
        {
            c: (getattr(cr, c).isoformat() if c == "expiry" and getattr(cr, c) else getattr(cr, c))
            for c in _CONTRACT_COLS
        }
        for cr in contract_rows
    ]

    return build_flow_payload(root, day, prints, daily=daily, contracts=contracts, top=top)
