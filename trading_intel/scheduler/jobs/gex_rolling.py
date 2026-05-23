"""Scheduled job (EOD): long-dated rolling GEX for the watchlist.

For each watchlist symbol, pulls a wide options chain (~6 months of
expirations), computes net signed gxoi totalled across the window plus a
per-expiration term structure, and writes:
- one ``gex_rolling`` row (the 6-month total — directional-flow time series)
- one ``gex_term`` row per expiration (the term structure)

Idempotent: ``INSERT ... ON CONFLICT DO NOTHING`` keyed on natural keys, with
``ts`` floored to the day so a same-day re-run does not duplicate (CLAUDE.md
rule 5). Data collection only — emits no signals (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.gex_rolling
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
from trading_intel.greeks.rolling import compute_rolling_gex
from trading_intel.memory.models import GexRolling, GexTerm
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_SOURCE = "convex"
DEFAULT_WINDOW_DAYS = 180  # ~6 months


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> None:
    """Snapshot long-dated rolling GEX (total + term structure) for the watchlist."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="gex_rolling")

    ts = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    symbols = effective_symbols(session, settings)
    bound.info(
        "gex_rolling.start", ts=ts.isoformat(), symbol_count=len(symbols),
        window_days=window_days,
    )

    written = 0
    failed = 0
    for symbol in symbols:
        try:
            chain_df = source.chain_long(symbol)  # type: ignore[attr-defined]
            spot = source._spot(symbol)  # type: ignore[attr-defined]
        except (TradingIntelError, AttributeError) as exc:
            failed += 1
            bound.warning("gex_rolling.symbol_failed", symbol=symbol, error=str(exc))
            continue

        rolling = compute_rolling_gex(chain_df, window_days=window_days)
        if not rolling["term"]:
            bound.warning("gex_rolling.empty", symbol=symbol)
            continue

        header = (
            pg_insert(GexRolling)
            .values(
                symbol=symbol,
                ts=ts,
                spot=spot,
                window_days=window_days,
                gex_total=rolling["total"],
                n_expirations=rolling["n_expirations"],
                source=_SOURCE,
            )
            .on_conflict_do_nothing(index_elements=["symbol", "ts", "source"])
        )
        session.execute(header)

        term_rows = [
            {
                "symbol": symbol,
                "ts": ts,
                "expiration": t["expiration"],
                "dte": t["dte"],
                "gex": t["gex"],
                "source": _SOURCE,
            }
            for t in rolling["term"]
        ]
        if term_rows:
            detail = pg_insert(GexTerm).values(term_rows).on_conflict_do_nothing(
                index_elements=["symbol", "ts", "source", "expiration"]
            )
            session.execute(detail)

        written += 1
        bound.debug(
            "gex_rolling.row",
            symbol=symbol,
            gex_total=rolling["total"],
            n_expirations=rolling["n_expirations"],
        )

    session.commit()
    bound.info("gex_rolling.done", written=written, failed=failed)


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
