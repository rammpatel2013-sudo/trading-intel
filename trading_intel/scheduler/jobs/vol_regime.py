"""Scheduled job (EOD): write today's vol regime to ``signals``.

Thin wrapper around ``strategies.vol_regime`` — the regime classifier reads
today's ``index_skew_daily`` row (which the ``index_skew`` job has just
populated at 16:50 ET) and writes an ``INDEX_VOL_REGIME`` state signal plus, on
regime change, a ``VOL_REGIME_TRANSITION`` signal.

Idempotent (CLAUDE.md rule 5): a repeat call on the same date with the same
label is a no-op; the strategy's ``emit_signals`` checks for an existing row
before insert.

Manual run:
    python -m trading_intel.scheduler.jobs.vol_regime
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.strategies import vol_regime as strategy

log = structlog.get_logger(__name__)


def run(session: Session, *, settings: Settings | None = None) -> None:
    """Run the regime classifier against the latest ``index_skew_daily`` row."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="vol_regime")
    bound.info("vol_regime.start")
    inserts = strategy.emit_signals(session)
    session.commit()
    bound.info("vol_regime.done", n_emitted=len(inserts))


if __name__ == "__main__":  # pragma: no cover
    settings = get_settings()
    factory = make_session_factory(settings)
    with factory() as session:
        run(session, settings=settings)
