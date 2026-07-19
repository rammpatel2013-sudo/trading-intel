"""Scheduled job (EOD): emit candidate swing setups -> ``signals``.

Thin wrapper over ``strategies.swing_options`` (the validated writer). Reads the
day's banked ``swing_features`` and appends per-name candidate signals, guarded to
once per day so re-runs don't duplicate (append-only generator + idempotent job,
CLAUDE.md rule 5).

Signals are flagged ``experimental=True`` until the Phase-6 backtest validates
them — the alerting layer must not promote experimental signals (FlashAlpha
rule 4). Runs on the NAS as a DSM task:
    python -m trading_intel.scheduler.jobs.swing_signals
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Signal
from trading_intel.strategies.swing_options import (
    DEFAULT_MIN_SCORE,
    SIGNAL_LONG,
    SIGNAL_SHORT,
    persist,
    run_all,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _already_emitted(session: Session, *, as_of: date) -> bool:
    ts = datetime.combine(as_of, datetime.min.time())
    n = session.execute(
        select(func.count())
        .select_from(Signal)
        .where(Signal.signal_type.in_((SIGNAL_LONG, SIGNAL_SHORT)), Signal.ts == ts)
    ).scalar_one()
    return bool(n)


def run(
    session: Session, *, as_of: date | None = None, min_score: float = DEFAULT_MIN_SCORE
) -> int:
    """Emit today's candidate swing signals (idempotent). Returns rows written."""
    as_of = as_of or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="swing_signals")
    if _already_emitted(session, as_of=as_of):
        bound.info("swing_signals.skip_duplicate", as_of=as_of.isoformat())
        return 0
    generated = run_all(session, as_of=as_of, min_score=min_score)
    n = persist(session, generated)
    bound.info("swing_signals.done", as_of=as_of.isoformat(), signals=n)
    return n


def main() -> None:
    """Manual/NAS entrypoint: wire Settings -> session, run once."""
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
    settings = get_settings()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session)


if __name__ == "__main__":
    main()
