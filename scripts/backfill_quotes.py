"""One-time daily-price backfill into ``quotes_daily`` (yfinance).

Pulls deep daily history (default ``QUOTES_BACKFILL_PERIOD`` ≈ 5y) for every
watchlist symbol, computes rv20/rv60, and idempotently upserts into
``quotes_daily``. Safe to re-run — settled bars are left untouched
(ON CONFLICT DO NOTHING). After this, the Ticker page serves price from the DB
instead of the live yfinance fallback, and the daily EOD job keeps it current.

Run (Windows; DATABASE_URL pointed at the NAS):
    .venv\\Scripts\\python scripts\\backfill_quotes.py
    .venv\\Scripts\\python scripts\\backfill_quotes.py --period max
    .venv\\Scripts\\python scripts\\backfill_quotes.py --symbol SPX --period 10y
"""
from __future__ import annotations

import argparse

import structlog

from trading_intel.clients.prices import YFinancePriceSource
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.scheduler.jobs import quotes_daily


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill daily price history.")
    parser.add_argument("--period", default=None, help="yfinance period (default: settings 5y)")
    parser.add_argument("--symbol", default=None, help="Backfill a single symbol (default: all)")
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    settings = get_settings()
    period = args.period or settings.QUOTES_BACKFILL_PERIOD
    prices = YFinancePriceSource()
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        if args.symbol:
            # Narrow the watchlist to one symbol for a targeted backfill.
            object.__setattr__(settings, "WATCHLIST", args.symbol.upper())
        quotes_daily.run(session, prices, settings=settings, period=period)
    print(f"Backfill complete (period={period}).")


if __name__ == "__main__":
    main()
