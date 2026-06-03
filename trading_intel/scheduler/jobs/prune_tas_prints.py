"""Scheduled job: prune stale ``tas_prints`` rows.

The raw market-wide tape capture is high-volume (every large print, all day).
The day-over-day flow analytics only need recent prints; the long-term value
lives in the small per-day roll-up, not the raw prints. This deletes
``tas_prints`` rows older than ``TAS_RETENTION_DAYS`` (default 30) to keep the
table bounded. Deletes are naturally idempotent (CLAUDE.md rule 5).

Manual run:
    python -m trading_intel.scheduler.jobs.prune_tas_prints
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import TasPrint
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_DEFAULT_RETENTION_DAYS = 30


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """Delete ``tas_prints`` rows older than the retention window.

    Returns the number of rows deleted. ``now`` is injectable for testing. Falls
    back to a ``TAS_RETENTION_DAYS`` setting if present, else 30 days.
    """
    settings = settings or get_settings()
    if retention_days is not None:
        days = retention_days
    else:
        days = getattr(settings, "TAS_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS)
    cutoff = (now or eastern_now()) - timedelta(days=days)
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="prune_tas_prints")

    result = session.execute(delete(TasPrint).where(TasPrint.ts < cutoff))
    deleted = int(result.rowcount or 0)
    session.commit()
    bound.info("prune_tas_prints.done", cutoff=cutoff.isoformat(), deleted=deleted, days=days)
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
    print(f"Pruned {deleted} tas_prints rows.")


if __name__ == "__main__":
    main()
