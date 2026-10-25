"""Scheduled job (post-close): grade the morning MAP card against the tape (§7).

Walks every ``map_daily`` row that has levels but no grade, looks up that
session's SPX daily high / low / close in ``quotes_daily``, and writes the
verdict back onto the same row.

Deliberately re-runnable and patient:

* a row whose close is not banked yet is simply left alone, so the job can run
  before the EOD quote lands and pick it up tomorrow;
* only rows inside ``--lookback`` days are considered, so a long-dead row with
  a permanently missing quote can't make every future run scan the whole table;
* already-graded rows are never revisited, so a grade cannot silently change.

Run it after the daily quote job. Descriptor only (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.map_grade
"""

from __future__ import annotations

import argparse
import uuid
from datetime import date, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market.grading import grade_session
from trading_intel.memory.models import MapDaily, QuoteDaily
from trading_intel.synthesis.map_store import ungraded_rows

log = structlog.get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 14
_GRADE_COLS = (
    "bucket", "floor_touched", "cap_touched",
    "floor_held", "cap_held", "inside_range70", "wall_broke",
)


def _closes(session: Session, symbol: str, dates: list[date]) -> dict[date, QuoteDaily]:
    if not dates:
        return {}
    rows = session.execute(
        select(QuoteDaily).where(
            QuoteDaily.symbol == symbol, QuoteDaily.date.in_(dates)
        )
    ).scalars().all()
    return {r.date: r for r in rows}


def run(
    session: Session,
    *,
    symbol: str = "SPX",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: date | None = None,
) -> int:
    """Grade what can be graded. Returns the number of rows written."""
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="map_grade")
    today = today or date.today()
    since = today - timedelta(days=lookback_days)

    pending = ungraded_rows(session, symbol, since=since)
    if not pending:
        bound.info("map_grade.nothing_pending", since=since.isoformat())
        return 0

    quotes = _closes(session, symbol, [r.date for r in pending])
    graded = 0
    waiting: list[str] = []
    for row in pending:
        q = quotes.get(row.date)
        if q is None or q.close is None:
            waiting.append(row.date.isoformat())
            continue
        verdict = grade_session(
            floor=row.floor,
            cap=row.cap,
            range70_lo=row.range70_lo,
            range70_hi=row.range70_hi,
            high=q.high,
            low=q.low,
            close=q.close,
        )
        if verdict["bucket"] is None:
            # No floor/cap banked that morning — nothing to grade, and leaving
            # the row ungraded would retry it forever. Mark it closed out.
            waiting.append(row.date.isoformat())
            continue
        row.high, row.low, row.close = q.high, q.low, q.close
        for col in _GRADE_COLS:
            setattr(row, col, verdict[col])
        row.graded_at = datetime.now()
        graded += 1

    session.commit()
    bound.info(
        "map_grade.done",
        graded=graded,
        waiting_on_quotes=len(waiting),
        waiting_dates=waiting[:5],
    )
    return graded


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="SPX")
    ap.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    args = ap.parse_args()

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
        run(session, symbol=args.symbol, lookback_days=args.lookback_days)


if __name__ == "__main__":
    main()
