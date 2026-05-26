"""Scheduled job: prune stale live-GEX rows.

The intraday ``live_gex`` rows are written every ~10 minutes for the configured
symbols (high volume, only useful for the current session). Historical GEX lives
in ``greeks_chain`` / ``greeks_snapshots``, so this deletes ``live_gex`` rows
older than ``LIVE_GEX_RETENTION_HOURS`` (default 24) to keep the table small and
avoid confusing the live view with stale data. Deletes are idempotent.

Manual run:
    python -m trading_intel.scheduler.jobs.prune_live_gex
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import LiveGex
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    retention_hours: int | None = None,
    now: datetime | None = None,
) -> int:
    """Delete ``live_gex`` rows older than the retention window. Returns row count."""
    settings = settings or get_settings()
    hours = retention_hours if retention_hours is not None else settings.LIVE_GEX_RETENTION_HOURS
    cutoff = (now or eastern_now()) - timedelta(hours=hours)
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="prune_live_gex")

    result = session.execute(delete(LiveGex).where(LiveGex.ts < cutoff))
    deleted = int(result.rowcount or 0)
    session.commit()
    bound.info("prune_live_gex.done", cutoff=cutoff.isoformat(), deleted=deleted, hours=hours)
    return deleted


def main() -> None:
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
    print(f"Pruned {deleted} live_gex rows.")


if __name__ == "__main__":
    main()
