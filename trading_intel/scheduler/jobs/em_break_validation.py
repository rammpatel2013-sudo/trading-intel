"""Scheduled job (weekly): bank realized outcomes for EM_BREAK_REENTRY signals.

Path (c) of the P6 backtest: walk ``quotes_daily`` forward from each banked re-entry
signal, record whether the call-wall target or the put-wall stop hit first (with the
R-multiple) into ``signal_outcomes``, and log the rolling hit-rate / expectancy. OPEN
trades are re-evaluated and overwritten until they close (idempotent upsert on
``signal_id`` — rule 5). Read-only w.r.t. ``signals`` — it only writes the outcomes
ledger.

The accumulated ledger is what lets us eventually drop ``experimental=True`` on the
signal; the success criteria live in ``docs/em_break_backtest.md``.

Manual run:
    python -m trading_intel.scheduler.jobs.em_break_validation
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.backtest.cases import DEFAULT_MAX_DAYS, SIGNAL_TYPE, case_from_signal
from trading_intel.backtest.em_break import summarize
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import Signal, SignalOutcome
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

#: Wait this many sessions after a signal before recording an outcome (let the trade
#: breathe); OPEN rows are still upserted and refreshed on later runs.
MIN_AGE_DAYS = 1

_UPDATE_COLS = (
    "symbol",
    "signal_type",
    "entry_date",
    "entry",
    "target",
    "stop",
    "result",
    "exit_date",
    "exit_price",
    "days_held",
    "r_multiple",
    "conviction",
    "max_days",
    "evaluated_at",
)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    max_days: int = DEFAULT_MAX_DAYS,
    min_age_days: int = MIN_AGE_DAYS,
) -> None:
    """Evaluate banked re-entry signals and upsert their realized outcomes."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="em_break_validation")
    cutoff = datetime.combine(
        eastern_now().date() - timedelta(days=max(0, min_age_days)), datetime.min.time()
    )
    sigs = (
        session.execute(
            select(Signal)
            .where(Signal.signal_type == SIGNAL_TYPE, Signal.ts <= cutoff)
            .order_by(Signal.ts.asc())
        )
        .scalars()
        .all()
    )

    rows: list[dict] = []
    outcomes = []
    for sig in sigs:
        res = case_from_signal(session, sig, max_days=max_days)
        if res.outcome is None:
            continue
        oc = res.outcome
        outcomes.append(oc)
        rows.append(
            {
                "signal_id": sig.id,
                "symbol": res.symbol,
                "signal_type": SIGNAL_TYPE,
                "entry_date": res.entry_date,
                "entry": oc.entry,
                "target": oc.target,
                "stop": oc.stop,
                "result": oc.result,
                "exit_date": oc.exit_date,
                "exit_price": oc.exit_price,
                "days_held": oc.days_held,
                "r_multiple": oc.r_multiple,
                "conviction": res.conviction,
                "max_days": max_days,
                "evaluated_at": datetime.utcnow(),
            }
        )

    if rows:
        stmt = pg_insert(SignalOutcome).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["signal_id"],
            set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
        )
        session.execute(stmt)
        session.commit()

    summary = summarize(outcomes)
    bound.info(
        "em_break_validation.done",
        n_signals=len(sigs),
        n_scored=len(rows),
        n_closed=summary["n_closed"],
        hit_rate=summary["hit_rate"],
        avg_r=summary["avg_r"],
    )


def main() -> None:
    """Manual entrypoint: wire Settings -> session, run once."""
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
