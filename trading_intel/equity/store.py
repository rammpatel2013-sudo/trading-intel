"""DB helpers for the equity tables: idempotent upserts (rule 5) + loaders.

Postgres in production, SQLite in tests - ``upsert`` picks the dialect's ON CONFLICT.
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.equity.universe import Interval
from trading_intel.memory.models_equity import (
    EquityBar,
    EquityFundamental,
    EquityMember,
    EquityProfile,
)


def _ins(session: Session):  # noqa: ANN202
    bind = session.get_bind()
    return sqlite_insert if bind.dialect.name == "sqlite" else pg_insert


def upsert(
    session: Session,
    model: Any,
    rows: Sequence[Mapping],
    keys: Sequence[str],
    *,
    chunk: int = 1000,
    commit: bool = True,
) -> int:
    """INSERT ... ON CONFLICT (keys) DO UPDATE every other column. Returns rows written."""
    if not rows:
        return 0
    ins = _ins(session)
    allk = list(dict.fromkeys(k for r in rows for k in r))  # multi-row VALUES needs one key set
    if any(len(r) != len(allk) for r in rows):
        rows = [{k: r.get(k) for k in allk} for r in rows]
    cols = [c for c in allk if c not in keys]
    n = 0
    for i in range(0, len(rows), chunk):
        part = [dict(r) for r in rows[i : i + chunk]]
        stmt = ins(model).values(part)
        stmt = (
            stmt.on_conflict_do_update(
                index_elements=list(keys), set_={c: stmt.excluded[c] for c in cols}
            )
            if cols
            else stmt.on_conflict_do_nothing(index_elements=list(keys))
        )
        session.execute(stmt)
        n += len(part)
    if commit:
        session.commit()
    return n


# ── members ─────────────────────────────────────────────────────────────
def replace_members(session: Session, intervals: Iterable[Interval]) -> int:
    """Membership is derived from today's vendor lists, so it is rebuilt whole (one commit)."""
    rows = [
        {
            "symbol": iv.symbol,
            "index_code": iv.index_code,
            "start_date": iv.start_date,
            "end_date": iv.end_date,
            "source": iv.source,
        }
        for iv in intervals
    ]
    session.execute(delete(EquityMember))
    if rows:
        session.execute(_ins(session)(EquityMember).values(rows))
    session.commit()
    return len(rows)


def load_members(session: Session) -> list[Interval]:
    rows = session.execute(select(EquityMember)).scalars().all()
    return [Interval(r.symbol, r.index_code, r.start_date, r.end_date, r.source) for r in rows]


def load_profiles(session: Session) -> dict[str, EquityProfile]:
    return {p.symbol: p for p in session.execute(select(EquityProfile)).scalars().all()}


# ── bars + fundamentals ─────────────────────────────────────────────────
def last_bar_dates(session: Session) -> dict[str, date]:
    rows = session.execute(
        select(EquityBar.symbol, func.max(EquityBar.date)).group_by(EquityBar.symbol)
    ).all()
    return {s: d for s, d in rows}


def load_fundamentals(
    session: Session, symbols: Iterable[str] | None = None
) -> dict[str, list[dict]]:
    q = select(EquityFundamental)
    if symbols is not None:
        q = q.where(EquityFundamental.symbol.in_(list(symbols)))
    out: dict[str, list[dict]] = {}
    for r in session.execute(
        q.order_by(EquityFundamental.symbol, EquityFundamental.period_end)
    ).scalars():
        out.setdefault(r.symbol, []).append(
            {c.name: getattr(r, c.name) for c in EquityFundamental.__table__.columns}
        )
    return out


def shares_asof(funds: Sequence[Mapping]) -> tuple[list[date], list[float]]:
    """Step function (available_date -> diluted shares), sorted, for point-in-time lookups."""
    pts = sorted(
        (f["available_date"], f["shares_diluted"])
        for f in funds
        if f.get("available_date") and f.get("shares_diluted")
    )
    return [p[0] for p in pts], [float(p[1]) for p in pts]


def attach_market_cap(bars: Sequence[dict], funds: Sequence[Mapping]) -> list[dict]:
    """market_cap = close x the diluted share count public on that day (first known before)."""
    days, shares = shares_asof(funds)
    out = []
    for b in bars:
        mc = None
        if days:
            i = bisect.bisect_right(days, b["date"]) - 1
            sh = shares[i] if i >= 0 else shares[0]  # before the first filing: earliest count
            mc = b["close"] * sh
        out.append({**b, "market_cap": mc})
    return out


def load_bars(
    session: Session, *, start: date | None = None, symbols: Iterable[str] | None = None
) -> list[tuple]:
    """(symbol, date, open, high, low, close, volume, market_cap) rows ordered by date."""
    q = select(
        EquityBar.symbol,
        EquityBar.date,
        EquityBar.open,
        EquityBar.high,
        EquityBar.low,
        EquityBar.close,
        EquityBar.volume,
        EquityBar.market_cap,
    )
    if start is not None:
        q = q.where(EquityBar.date >= start)
    if symbols is not None:
        q = q.where(EquityBar.symbol.in_(list(symbols)))
    return list(session.execute(q.order_by(EquityBar.date)).all())
