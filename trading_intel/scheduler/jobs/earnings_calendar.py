"""Scheduled job (daily): ConvexValue earn_cal -> earnings_events.

The keystone anchor for the EM-break / gamma-burn-off system: banks the forward
earnings calendar so the pre-earnings straddle collector knows which names to
snapshot and the EM-break read knows a name is "post-earnings". No new vendor —
the same ConvexValue pro login (rule 1), via ``ConvexAppClient.upcoming_earnings``.

Idempotent: ``INSERT … ON CONFLICT (symbol, date) DO UPDATE`` (rule 5) — safe to
re-run; refreshes the BMO/AMC time. Descriptive data only (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.earnings_calendar
"""

from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import EarningsCalendarSource
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import EarningsEvent
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def build_rows(
    source: EarningsCalendarSource, settings: Settings, *, days: int | None = None
) -> list[dict]:
    """Pull the forward earnings calendar; de-dupe on (symbol, date) keeping last."""
    days = days or settings.EARNINGS_LOOKAHEAD_DAYS
    events = source.upcoming_earnings(days=days)
    dedup: dict[tuple[str, date], dict] = {}
    for e in events:
        dedup[(e.symbol, e.date)] = {
            "symbol": e.symbol,
            "date": e.date,
            "time": e.session,
        }
    return list(dedup.values())


def _upsert(session: Session, rows: list[dict]) -> None:
    """Idempotent upsert into ``earnings_events`` (refresh time on (symbol, date))."""
    if not rows:
        return
    stmt = pg_insert(EarningsEvent).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "date"],
        set_={"time": stmt.excluded["time"]},
    )
    session.execute(stmt)


def run(session: Session, source: EarningsCalendarSource, *, settings: Settings | None = None) -> None:
    """Snapshot the forward earnings calendar and upsert it."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="earnings_calendar")
    rows = build_rows(source, settings)
    _upsert(session, rows)
    session.commit()
    bound.info("earnings_calendar.done", rows=len(rows), days=settings.EARNINGS_LOOKAHEAD_DAYS)


def main() -> None:
    """Manual entrypoint: wire Settings -> ConvexAppClient -> session, run once."""
    from trading_intel.clients.convex_app import ConvexAppClient
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    source = ConvexAppClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
