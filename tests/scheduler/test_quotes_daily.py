"""Tests for the quotes_daily price backfill/refresh job — SQLite, fake source."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, event, func, select

from trading_intel.memory.models_equity import EquityBar
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
        REGIME_REF_SYMBOLS="",
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
    EquityBar.__table__.create(engine)   # 2026-11-19 A2 step 7a: quotes_daily also writes equity_bars
    with Session(engine) as s:
        yield s


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(EquityBar)).scalar_one()


# 2026-11-19 (DB wave A2 step 7b): the job writes equity_bars; quotes_daily is a VIEW over it in Postgres with
# rv20 / rv60 computed in SQL (alembic 0065) - SQLite has no view, so these tests read equity_bars.


def test_run_inserts_history(session: Session):
    quotes_daily.run(session, FakePriceSource(n=80), settings=_settings(), period="max")
    rows = list(session.execute(select(EquityBar).order_by(EquityBar.date)).scalars())
    assert len(rows) == 80
    assert session.get(Ticker, "SPY") is not None   # the view lists only watchlist (tickers) symbols
    assert rows[-1].volume == 80 * 1000
    assert _count_quotes(session) == 0              # nothing written to the old table any more


def _count_quotes(session: Session) -> int:
    return session.execute(select(func.count()).select_from(QuoteDaily)).scalar_one()


def test_run_is_idempotent(session: Session):
    s = _settings()
    quotes_daily.run(session, FakePriceSource(n=40), settings=s, period="max")
    n1 = _count(session)
    quotes_daily.run(session, FakePriceSource(n=40), settings=s, period="max")
    assert _count(session) == n1 == 40  # ON CONFLICT DO NOTHING


def test_run_handles_index_without_volume(session: Session):
    quotes_daily.run(
        session, FakePriceSource(n=30, with_volume=False), settings=_settings(), period="max"
    )
    rows = list(session.execute(select(EquityBar)).scalars())
    assert len(rows) == 30
    assert all(r.volume == 0 for r in rows)  # NaN volume coerced to 0


def test_run_symbols_override(session: Session):
    # Watchlist says SPY, but we explicitly backfill only TSLA.
    quotes_daily.run(session, FakePriceSource(n=30), settings=_settings(), period="max",
                     symbols=["TSLA"])
    rows = list(session.execute(select(EquityBar)).scalars())
    assert rows and {r.symbol for r in rows} == {"TSLA"}


def test_default_symbols_include_regime_refs_once() -> None:
    s = Settings(
        CONVEX_EMAIL="x@example.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://example.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY", REGIME_REF_SYMBOLS="SPY,RSP",
    )
    assert s.regime_ref_symbols == ["SPY", "RSP"]
