"""Scheduled job (evening): SqueezeMetrics DIX + GEX -> ``squeezemetrics_daily``.

One plain HTTPS GET of the free public ``DIX.csv`` (no key, no Convex), then an
idempotent upsert of every row the table doesn't have yet (the first run loads
the whole 2011→ history; later runs add the newest session). Safe to re-run;
safe on weekends (nothing new → nothing written). If the download fails the job
logs a WARNING and exits 0 so the DSM chain continues; the fear-release label
then shows "n/a" for the day.

DSM: run after ~18:30 ET (they post after the close) and again at ~07:30 ET as
a retry for late postings. See scripts/nas/run_job.sh.

Manual run:
    python -m trading_intel.scheduler.jobs.squeezemetrics
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.market.squeezemetrics import parse_dix_csv
from trading_intel.memory.models import SqueezeMetricsDaily

log = structlog.get_logger(__name__)

DIX_CSV_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"
_UPDATE_COLS = ("spx_price", "dix", "gex")


def fetch_dix_csv(*, timeout: float = 30.0, client: object | None = None) -> str:
    """Download the CSV body. ``client`` is an httpx-like object for tests."""
    if client is not None:
        resp = client.get(DIX_CSV_URL)
    else:
        import httpx

        resp = httpx.get(DIX_CSV_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _upsert(session: Session, records: list[dict]) -> None:
    if not records:
        return
    stmt = pg_insert(SqueezeMetricsDaily).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(session: Session, *, client: object | None = None) -> int:
    """Fetch, parse, upsert rows newer than the last stored date. Returns rows written."""
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="squeezemetrics")
    try:
        text = fetch_dix_csv(client=client)
    except Exception as exc:  # network / HTTP — degrade, never fail the chain
        bound.warning("squeezemetrics.fetch_failed", error=str(exc))
        return 0
    rows = parse_dix_csv(text)
    if not rows:
        bound.warning("squeezemetrics.empty_csv")
        return 0

    last = session.execute(select(func.max(SqueezeMetricsDaily.date))).scalar()
    # Re-write the last 5 stored sessions too: SqueezeMetrics occasionally
    # revises the newest print after the fact.
    cutoff = None
    if last is not None:
        older = [r["date"] for r in rows if r["date"] <= last]
        cutoff = older[-5] if len(older) >= 5 else (older[0] if older else None)
    new = [r for r in rows if cutoff is None or r["date"] >= cutoff]
    _upsert(session, new)
    session.commit()
    bound.info(
        "squeezemetrics.done",
        rows_written=len(new),
        first=rows[0]["date"].isoformat(),
        last=rows[-1]["date"].isoformat(),
    )
    return len(new)


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    session_factory = make_session_factory(get_settings())
    with session_factory() as session:
        run(session)


if __name__ == "__main__":
    main()
