"""Scheduled job: prune stale intraday 0DTE/1DTE flow rows.

The per-strike ``intraday_flow`` rows are written every 5 minutes for
SPX/SPY/QQQ — high volume, and only useful for the recent regime. Durable
history lives in ``greeks_snapshots`` / ``quotes_daily``, so this job deletes
``intraday_flow`` rows older than ``INTRADAY_RETENTION_HOURS`` (default 48) to
keep the table small. Deletes are naturally idempotent (CLAUDE.md rule 5).

Manual run:
    python -m trading_intel.scheduler.jobs.prune_intraday
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import IntradayFlow
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    retention_hours: int | None = None,
    now: datetime | None = None,
) -> int:
    """Delete ``intraday_flow`` rows older than the retention window.

    Returns the number of rows deleted. ``now`` is injectable for testing.
    """
    settings = settings or get_settings()
    hours = retention_hours if retention_hours is not None else settings.INTRADAY_RETENTION_HOURS
    cutoff = (now or eastern_now()) - timedelta(hours=hours)
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="prune_intraday")

    result = session.execute(delete(IntradayFlow).where(IntradayFlow.ts < cutoff))
    deleted = int(result.rowcount or 0)
    session.commit()
    bound.info("prune_intraday.done", cutoff=cutoff.isoformat(), deleted=deleted, hours=hours)
    return deleted


def main() -> None:
    """Manual entrypoint: wire Settings -> session, prune once."""
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
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        deleted = run(session, settings=settings)
    print(f"Pruned {deleted} intraday_flow rows.")


if __name__ == "__main__":
    main()
