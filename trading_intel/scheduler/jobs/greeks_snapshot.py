"""Scheduled job: snapshot aggregate Greeks for the watchlist.

Pulls GEX/DEX/VEX/CHEX + flip point for every watchlist symbol from the
configured ``OptionsDataSource`` and writes one ``greeks_snapshots`` row per
ticker. Idempotent: ``INSERT ... ON CONFLICT (symbol, ts, source) DO NOTHING``
(CLAUDE.md rule 5), with ``ts`` floored to the minute so re-running the same
scheduled slot does not duplicate rows.

This is data collection only — it emits no signals/alerts (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.greeks_snapshot
"""
from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import GreeksSnapshot

log = structlog.get_logger(__name__)

_SOURCE = "convex"


def run(session: Session, source: OptionsDataSource, *, settings: Settings | None = None) -> None:
    """Snapshot watchlist Greeks into ``greeks_snapshots``.

    Args:
        session: an open SQLAlchemy session (committed here).
        source: any ``OptionsDataSource`` implementation.
        settings: optional override; defaults to the process settings.
    """
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="greeks_snapshot")

    ts = datetime.now().replace(second=0, microsecond=0)
    symbols = settings.watchlist_symbols
    bound.info("greeks_snapshot.start", ts=ts.isoformat(), symbol_count=len(symbols))

    written = 0
    failed = 0
    for symbol in symbols:
        try:
            exposures = source.exposures(symbol)
        except TradingIntelError as exc:
            failed += 1
            bound.warning("greeks_snapshot.symbol_failed", symbol=symbol, error=str(exc))
            continue

        if not exposures:
            bound.warning("greeks_snapshot.empty", symbol=symbol)
            continue

        stmt = (
            pg_insert(GreeksSnapshot)
            .values(
                symbol=symbol,
                ts=ts,
                spot=exposures.get("spot"),
                gex_total=exposures.get("gex_total"),
                dex_total=exposures.get("dex_total"),
                vex_total=exposures.get("vex_total"),
                chex_total=exposures.get("chex_total"),
                gex_flip=exposures.get("gex_flip"),
                gex_rvol_ratio=None,  # needs 20d realized vol — populated later
                atm_iv=exposures.get("atm_iv"),
                source=_SOURCE,
            )
            .on_conflict_do_nothing(index_elements=["symbol", "ts", "source"])
        )
        session.execute(stmt)
        written += 1
        bound.debug(
            "greeks_snapshot.row",
            symbol=symbol,
            spot=exposures.get("spot"),
            gex_total=exposures.get("gex_total"),
            gex_flip=exposures.get("gex_flip"),
        )

    session.commit()
    bound.info("greeks_snapshot.done", written=written, failed=failed)


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
