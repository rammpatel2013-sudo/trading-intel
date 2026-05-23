"""Scheduled job: daily price history into ``quotes_daily``.

Pulls daily OHLCV for every watchlist symbol from a ``PriceDataSource``
(yfinance by default), computes annualized realized vol (rv20/rv60), and upserts
one ``quotes_daily`` row per (symbol, date). Idempotent: ``INSERT ... ON CONFLICT
(symbol, date) DO NOTHING`` (CLAUDE.md rule 5), so a re-run — or the daily job
overlapping the backfill — never duplicates or rewrites a settled bar.

Used two ways:

* one-time backfill (``scripts/backfill_quotes.py``, ``period`` = ~5y);
* a daily EOD refresh (short ``period``, just enough to append the new session
  and compute its rv).

Data collection only — emits no signals/alerts (FlashAlpha rule 4).

Manual run (daily refresh window):
    python -m trading_intel.scheduler.jobs.quotes_daily
"""
from __future__ import annotations

import uuid

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import PriceDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import QuoteDaily, Ticker
from trading_intel.prices.realized_vol import add_realized_vol
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_UQ_COLS = ["symbol", "date"]
_PRICE_COLS = ("open", "high", "low", "close")


def _history_to_records(hist: pd.DataFrame, *, symbol: str) -> list[dict]:
    """Map a daily OHLCV history (+rv) frame to ``quotes_daily`` rows."""
    needed = {"date", "close"}
    if hist is None or hist.empty or not needed.issubset(hist.columns):
        return []
    df = add_realized_vol(hist, windows=(20, 60))
    df = df.dropna(subset=["date", "close"])
    records: list[dict] = []
    for _, row in df.iterrows():
        px = {c: pd.to_numeric(row.get(c), errors="coerce") for c in _PRICE_COLS}
        close = px["close"]
        if not pd.notna(close):
            continue
        volume = pd.to_numeric(row.get("volume"), errors="coerce")
        rv20 = pd.to_numeric(row.get("rv20"), errors="coerce")
        rv60 = pd.to_numeric(row.get("rv60"), errors="coerce")
        records.append(
            {
                "symbol": symbol,
                "date": pd.Timestamp(row["date"]).date(),
                "open": _or_close(px["open"], close),
                "high": _or_close(px["high"], close),
                "low": _or_close(px["low"], close),
                "close": float(close),
                "volume": int(volume) if pd.notna(volume) else 0,
                "rv20": float(rv20) if pd.notna(rv20) else None,
                "rv60": float(rv60) if pd.notna(rv60) else None,
            }
        )
    return records


def _or_close(value: object, close: float) -> float:
    """OHLC fallback: use ``value`` when present, else the close (e.g. index bars)."""
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else float(close)


def _ensure_tickers(session: Session, symbols: list[str]) -> None:
    """Idempotently seed parent ``tickers`` rows.

    ``quotes_daily.symbol`` has a FK to ``tickers.symbol``; without the parent
    row Postgres rejects the insert (the greeks tables carry no such FK, which
    is why they collect fine). ``is_active`` is NOT NULL, so it is set here.
    """
    if not symbols:
        return
    stmt = pg_insert(Ticker).values(
        [{"symbol": s, "is_active": True} for s in symbols]
    ).on_conflict_do_nothing(index_elements=["symbol"])
    session.execute(stmt)


def run(
    session: Session,
    prices: PriceDataSource,
    *,
    settings: Settings | None = None,
    period: str | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Pull + upsert daily price history for the watchlist.

    Args:
        session: an open SQLAlchemy session (committed here).
        prices: any ``PriceDataSource`` implementation.
        settings: optional override; defaults to the process settings.
        period: vendor history window; defaults to ``QUOTES_REFRESH_PERIOD``
            (the daily job). The backfill script passes ``QUOTES_BACKFILL_PERIOD``.
        symbols: explicit symbol list (e.g. just-discovered research tickers);
            defaults to the effective watchlist.
    """
    settings = settings or get_settings()
    period = period or settings.QUOTES_REFRESH_PERIOD
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="quotes_daily")
    symbols = symbols if symbols is not None else effective_symbols(session, settings)
    bound.info("quotes_daily.start", period=period, symbol_count=len(symbols))
    _ensure_tickers(session, symbols)

    rows_written = 0
    failed = 0
    for symbol in symbols:
        try:
            hist = prices.daily_history(symbol, period=period)
        except TradingIntelError as exc:
            failed += 1
            bound.warning("quotes_daily.symbol_failed", symbol=symbol, error=str(exc))
            continue

        records = _history_to_records(hist, symbol=symbol)
        if not records:
            bound.warning("quotes_daily.empty", symbol=symbol)
            continue

        stmt = pg_insert(QuoteDaily).values(records).on_conflict_do_nothing(
            index_elements=_UQ_COLS
        )
        session.execute(stmt)
        rows_written += len(records)
        bound.debug("quotes_daily.symbol", symbol=symbol, rows=len(records))

    session.commit()
    bound.info("quotes_daily.done", rows=rows_written, failed=failed)


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> yfinance, daily refresh."""
    from trading_intel.clients.prices import YFinancePriceSource
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
    prices = YFinancePriceSource()
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, prices, settings=settings)


if __name__ == "__main__":
    main()
