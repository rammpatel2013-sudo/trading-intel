"""Scheduled job: prune stale ``tas_prints`` rows.

The raw market-wide tape capture is high-volume (every large print, all day).
2026-11-22: the tape is RAW and has no history anywhere else (Convex is live-only), and the position book
(pos_book) needs it for months. Mithil's retention rule (11/15): 1 year in Postgres, then archive - never delete.
So: keep ``TAS_RETENTION_DAYS`` (default 365, never less than ``MIN_RETENTION_DAYS`` unless ``force``), and for
each older session first write ``reports/archive/tas_prints/<YYYY>/tas_prints_<YYYY-MM-DD>.csv.gz`` (all
columns), check the row count, and only then delete that session from the table. Re-runnable: a session
already archived with the same row count is not rewritten.

Manual run:
    python -m trading_intel.scheduler.jobs.prune_tas_prints
"""

from __future__ import annotations

import csv
import gzip
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import structlog
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import TasPrint
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_DEFAULT_RETENTION_DAYS = 365
MIN_RETENTION_DAYS = 365
ARCHIVE_DIR = Path("reports") / "archive" / "tas_prints"


def _archive_day(session: Session, d, base: Path) -> int:
    """Write one session to a gzip CSV; returns rows written (0 = nothing to archive)."""
    cols = [c.name for c in TasPrint.__table__.columns]
    rows = session.execute(select(TasPrint.__table__).where(TasPrint.trade_date == d)).all()
    if not rows:
        return 0
    out = base / f"{d:%Y}" / f"tas_prints_{d:%Y-%m-%d}.csv.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            w.writerow(["" if v is None else v for v in r])
    with gzip.open(tmp, "rt", encoding="utf-8") as fh:
        n = sum(1 for _ in fh) - 1
    if n != len(rows):
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"tas_prints archive {d}: wrote {n} of {len(rows)} rows")
    tmp.replace(out)
    return n


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
    archive_dir: Path | None = None,
    force: bool = False,
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
    if days < MIN_RETENTION_DAYS and not force:
        log.warning("prune_tas_prints.retention_raised", asked=days, used=MIN_RETENTION_DAYS)
        days = MIN_RETENTION_DAYS
    cutoff = ((now or eastern_now()) - timedelta(days=days)).date()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="prune_tas_prints")
    base = archive_dir or ARCHIVE_DIR
    old = [
        r[0]
        for r in session.execute(
            select(TasPrint.trade_date)
            .where(TasPrint.trade_date < cutoff)
            .distinct()
            .order_by(TasPrint.trade_date)
        )
    ]
    deleted = 0
    for d in old:
        n = _archive_day(session, d, base)
        result = session.execute(delete(TasPrint).where(TasPrint.trade_date == d))
        got = int(result.rowcount or 0)
        if got != n:
            session.rollback()
            raise RuntimeError(
                f"tas_prints prune {d}: archived {n} rows but delete hit {got} - rolled back"
            )
        session.commit()
        deleted += got
        bound.info("prune_tas_prints.day", day=str(d), archived=n)
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
