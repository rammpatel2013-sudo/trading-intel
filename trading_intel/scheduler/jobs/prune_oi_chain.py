"""Scheduled job: prune stale ``oi_chain_eod`` rows.

The wide (~180d) per-strike EOD chain is large (every strike x expiration x side
for the whole watchlist, daily). The day-over-day analytics only need the last
couple of snapshots; longer history is for ad-hoc study. This deletes
``oi_chain_eod`` rows older than ``OI_CHAIN_RETENTION_DAYS`` (default 90) to keep
the table bounded. Deletes are naturally idempotent (CLAUDE.md rule 5).

Manual run:
    python -m trading_intel.scheduler.jobs.prune_oi_chain
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import delete
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import OiChainEod

log = structlog.get_logger(__name__)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """Delete ``oi_chain_eod`` rows older than the retention window.

    Returns the number of rows deleted. ``now`` is injectable for testing.
    """
    settings = settings or get_settings()
    days = retention_days if retention_days is not None else settings.OI_CHAIN_RETENTION_DAYS
    cutoff = (now or datetime.now()) - timedelta(days=days)
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="prune_oi_chain")

    result = session.execute(delete(OiChainEod).where(OiChainEod.ts < cutoff))
    deleted = int(result.rowcount or 0)
    session.commit()
    bound.info("prune_oi_chain.done", cutoff=cutoff.isoformat(), deleted=deleted, days=days)
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
    print(f"Pruned {deleted} oi_chain_eod rows.")


if __name__ == "__main__":
    main()
