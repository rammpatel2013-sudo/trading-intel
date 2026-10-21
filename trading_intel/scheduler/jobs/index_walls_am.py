"""Scheduled job (AM): per-strike chain snapshot for the INDEX roots.

The index roots (SPX/SPY/QQQ) are excluded from the heavy EOD per-strike
persister (``CHAIN_EXCLUDE_ROOTS``) to save storage, so ``get_walls`` /
``get_straddle`` have no *fresh* per-strike chain for them — only a stale
snapshot from before the exclusion. Option OI settles overnight and is
published in the morning, so a SINGLE morning snapshot of the index chains
gives fresh dealer walls (and a priceable ATM straddle) for the daily brief:
once a day, three roots — trivial storage next to the intraday persisters.

The client pulls from the FRONT expiry (``clients.convex._front_request``), so the
snapshot carries today's same-day expiry as ``dte = 0`` — the 0DTE book the brief's
gamma map and the TOS cockpit read. Until 2026-10-22 SPX lost it (Convex index 0 was
already today at 08:35 and every pull started at index 1). After the close, readers
skip the expired dte-0 rows (``timeutils.same_day_expired``).

Since 2026-10-22 it also pulls the STOCKS that have a same-day expiry that morning
(names whose newest stored chain lists an expiry dated today — read from the DB, no
extra vendor call to find them; capped at ``_STOCK_CAP`` by OI). Their morning view then
has today's OI and the 0DTE as the front. The 18:00 ``oi_chain_eod`` run replaces these
rows with the EOD book for the same day, so readers never sum two books.

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
from datetime import date, datetime, time, timedelta

import structlog
from sqlalchemy import func, select
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
_STOCK_CAP = 30  # ~4-5 min of Convex calls at the 7/min limit; done before the 08:50 reports


def same_day_names(
    session: Session, settings: object, today: date, *, cap: int = _STOCK_CAP
) -> list[str]:
    """Non-index names whose newest stored chain lists an expiry dated ``today``.

    Those are the stocks with a 0DTE this morning. Ordered by OI in that expiry, capped.
    Reads only ``oi_chain_eod`` (last night's EOD book), so it costs no vendor call.
    """
    skip = {s.upper() for s in getattr(settings, "chain_exclude_roots", set())}
    skip |= {s.upper() for s in getattr(settings, "index_roots", [])}
    day0 = datetime.combine(today, time())
    rows = session.execute(
        select(OiChainEod.symbol, func.sum(OiChainEod.oi).label("oi"))
        .where(
            OiChainEod.expiry == today,
            OiChainEod.ts >= day0 - timedelta(days=6),
            OiChainEod.ts < day0,
            OiChainEod.source != _SOURCE,
        )
        .group_by(OiChainEod.symbol)
        .order_by(func.sum(OiChainEod.oi).desc())
    ).all()
    return [r.symbol for r in rows if r.symbol and r.symbol.upper() not in skip][:cap]


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    symbols: list[str] | None = None,
    include_stocks: bool = True,
) -> None:
    """Snapshot the index roots' (and 0DTE stocks') per-strike AM chain into ``oi_chain_eod``.

    An explicit ``symbols`` list snapshots exactly those names (no stock discovery).
    """
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="index_walls_am")

    ts = eastern_now().replace(hour=0, minute=0, second=0, microsecond=0)
    roots = symbols or list(getattr(settings, "index_roots", []))
    if symbols is None and include_stocks:
        try:
            stocks = same_day_names(session, settings, ts.date())
        except Exception as exc:  # noqa: BLE001 - discovery must never cost the index book
            session.rollback()
            stocks = []
            bound.warning("index_walls_am.stock_discovery_failed", error=str(exc))
        roots = roots + [s for s in stocks if s not in roots]
        bound.info("index_walls_am.stocks", n=len(stocks), names=stocks[:12])
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
