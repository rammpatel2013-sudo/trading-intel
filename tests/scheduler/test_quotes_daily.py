"""Tests for the quotes_daily price backfill/refresh job — SQLite, fake source."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import QuoteDaily, Ticker
from trading_intel.scheduler.jobs import quotes_daily


class FakePriceSource:
    """Returns a deterministic OHLCV history long enough to prime rv20/rv60."""

    def __init__(self, *, n: int = 80, with_volume: bool = True) -> None:
        self._n = n
        self._with_volume = with_volume

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-01", periods=self._n)
        close = 100 * np.exp(np.cumsum(np.full(self._n, 0.002)))
        df = pd.DataFrame(
            {
                "date": dates,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": (np.arange(self._n) + 1) * 1000 if self._with_volume else np.nan,
            }
        )
        return df


def _settings() -> Settings:
    return Settings(
        CONVEX_EMAIL="x@example.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://example.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY",
    )


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")

    # SQLite ignores FKs unless explicitly enabled; turn them on so the
    # quotes_daily -> tickers FK is actually enforced in tests.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Ticker.__table__.create(engine)
    QuoteDaily.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(QuoteDaily)).scalar_one()


def test_run_inserts_history_with_rv(session: Session):
    quotes_daily.run(session, FakePriceSource(n=80), settings=_settings(), period="max")
    rows = list(session.execute(select(QuoteDaily).order_by(QuoteDaily.date)).scalars())
    assert len(rows) == 80
    # Parent ticker row must have been seeded (else the FK insert fails).
    assert session.get(Ticker, "SPY") is not None
    # rv20 primes after 20 returns; the last row has it, the first does not.
    assert rows[0].rv20 is None
    assert rows[-1].rv20 is not None and rows[-1].rv20 > 0
    assert rows[-1].rv60 is not None
    assert rows[-1].volume == 80 * 1000


def test_run_is_idempotent(session: Session):
    s = _settings()
    quotes_daily.run(session, FakePriceSource(n=40), settings=s, period="max")
    n1 = _count(session)
    quotes_daily.run(session, FakePriceSource(n=40), settings=s, period="max")
    assert _count(session) == n1  # ON CONFLICT DO NOTHING


def test_run_handles_index_without_volume(session: Session):
    quotes_daily.run(
        session, FakePriceSource(n=30, with_volume=False), settings=_settings(), period="max"
    )
    rows = list(session.execute(select(QuoteDaily)).scalars())
    assert len(rows) == 30
    assert all(r.volume == 0 for r in rows)  # NaN volume coerced to 0


def test_run_symbols_override(session: Session):
    # Watchlist says SPY, but we explicitly backfill only TSLA.
    quotes_daily.run(session, FakePriceSource(n=30), settings=_settings(), period="max",
                     symbols=["TSLA"])
    rows = list(session.execute(select(QuoteDaily)).scalars())
    assert rows and {r.symbol for r in rows} == {"TSLA"}
