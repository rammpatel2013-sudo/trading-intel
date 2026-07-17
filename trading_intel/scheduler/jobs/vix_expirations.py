"""Scheduled job (EOD): refresh the standard VIX expiration calendar.

Computes the standard (monthly) VIX settlement schedule from
``vol.vix_calendar`` — fully deterministic (Cboe spec), so there is no vendor
call. Writes a sliding window of rows (a couple of months back for joins, plus
the next ``HORIZON_MONTHS`` ahead) into ``vix_expirations``. Idempotent: each
row is keyed on the settlement date with ``ON CONFLICT DO UPDATE`` (CLAUDE.md
rule 5). Descriptive calendar data — never a signal (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.vix_expirations
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import VixExpiration
from trading_intel.timeutils import eastern_now
from trading_intel.vol.vix_calendar import (
    third_friday,
    vix_expiration_for_settlement_month,
)

log = structlog.get_logger(__name__)

#: How many months forward to materialize.
HORIZON_MONTHS = 18
#: How many months back to keep (so recent expiries remain joinable).
LOOKBACK_MONTHS = 2

_UPDATE_COLS = ("spx_ref_expiry", "holiday_adjusted", "updated_at")


def _settlement_month_offset(year: int, month: int, offset: int) -> tuple[int, int]:
    """Return (year, month) shifted by ``offset`` months (offset may be negative)."""
    idx = (year * 12 + (month - 1)) + offset
    return idx // 12, idx % 12 + 1


def build_rows(as_of: date) -> list[dict]:
    """Materialize the expiration window centered on ``as_of``."""
    rows: list[dict] = []
    for off in range(-LOOKBACK_MONTHS, HORIZON_MONTHS + 1):
        y, m = _settlement_month_offset(as_of.year, as_of.month, off)
        # The paired SPX expiry is the third Friday of the *following* month.
        ny, nm = _settlement_month_offset(y, m, 1)
        spx_ref = third_friday(ny, nm)
        exp = vix_expiration_for_settlement_month(y, m)
        candidate = spx_ref - timedelta(days=30)
        rows.append(
            {
                "expiration": exp,
                "spx_ref_expiry": spx_ref,
                "holiday_adjusted": exp != candidate,
                "updated_at": as_of,
            }
        )
    return rows


def _upsert(session: Session, rows: list[dict]) -> None:
    if not rows:
        return
    stmt = pg_insert(VixExpiration).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["expiration"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(session: Session, *, settings: Settings | None = None, as_of: date | None = None) -> None:
    """Compute and upsert the standard VIX expiration calendar window."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="vix_expirations")

    as_of = as_of or eastern_now().date()
    rows = build_rows(as_of)
    _upsert(session, rows)
    session.commit()

    upcoming = [r["expiration"] for r in rows if r["expiration"] >= as_of]
    bound.info(
        "vix_expirations.done",
        as_of=as_of.isoformat(),
        n_rows=len(rows),
        next_expiry=upcoming[0].isoformat() if upcoming else None,
    )


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
        run(session, settings=settings)


if __name__ == "__main__":
    main()
