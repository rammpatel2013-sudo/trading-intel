"""Scheduled job (AM): per-strike chain snapshot for the INDEX roots.

The index roots (SPX/SPY/QQQ) are excluded from the heavy EOD per-strike
persister (``CHAIN_EXCLUDE_ROOTS``) to save storage, so ``get_walls`` /
``get_straddle`` have no *fresh* per-strike chain for them — only a stale
snapshot from before the exclusion. Option OI settles overnight and is
published in the morning, so a SINGLE morning snapshot of the index chains
gives fresh dealer walls (and a priceable ATM straddle) for the daily brief:
once a day, three roots — trivial storage next to the intraday persisters.

Writes to the same ``oi_chain_eod`` table (``ts`` floored to the day, distinct
``source`` so it never collides with anything), idempotent
``INSERT ... ON CONFLICT DO NOTHING`` (CLAUDE.md rule 5). ``get_walls`` reads the
newest ``ts`` for a symbol regardless of source, so this AM row becomes the live
wall set. Data collection only — no signals (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.index_walls_am
"""
from __future__ import annotations

import uuid

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import OiChainEod
from trading_intel.scheduler.jobs.oi_chain_eod import (
    DEFAULT_WINDOW_DAYS,
    _INSERT_BATCH,
    _UQ_COLS,
    _chain_to_records,
)
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_SOURCE = "convex_am"


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    symbols: list[str] | None = None,
) -> None:
    """Snapshot the index roots' per-strike AM chain into ``oi_chain_eod``."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="index_walls_am")

    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    roots = symbols or list(getattr(settings, "index_roots", []))
    bound.info("index_walls_am.start", ts=ts.isoformat(), roots=roots, window_days=window_days)

    rows_written = 0
    failed = 0
    for symbol in roots:
        try:
            chain = source.chain_long(symbol)  # type: ignore[attr-defined]
        except (TradingIntelError, AttributeError) as exc:
            failed += 1
            bound.warning("index_walls_am.symbol_failed", symbol=symbol, error=str(exc))
            continue

        records = _chain_to_records(
            chain, symbol=symbol, ts=ts, window_days=window_days, source=_SOURCE
        )
        if not records:
            bound.warning("index_walls_am.empty", symbol=symbol)
            continue

        dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
        _insert = sqlite_insert if dialect == "sqlite" else pg_insert
        for start in range(0, len(records), _INSERT_BATCH):
            batch = records[start : start + _INSERT_BATCH]
            stmt = _insert(OiChainEod).values(batch).on_conflict_do_nothing(
                index_elements=_UQ_COLS
            )
            session.execute(stmt)
        rows_written += len(records)
        bound.debug("index_walls_am.symbol", symbol=symbol, rows=len(records))

    session.commit()
    bound.info("index_walls_am.done", rows=rows_written, failed=failed)


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> ConvexClient, run once."""
    from trading_intel.clients.convex import ConvexClient
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

    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
