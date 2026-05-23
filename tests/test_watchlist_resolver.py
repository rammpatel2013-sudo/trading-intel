"""Tests for the effective-watchlist resolver."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import WatchlistEntry
from trading_intel.watchlist import effective_symbols, research_symbols


def _settings(watchlist: str = "SPY,QQQ") -> Settings:
    return Settings(
        CONVEX_EMAIL="x@e.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST=watchlist,
    )


def test_effective_symbols_unions_static_and_research():
    engine = create_engine("sqlite://")
    WatchlistEntry.__table__.create(engine)
    with Session(engine) as s:
        s.add_all([
            WatchlistEntry(symbol="NVDA", active=True),
            WatchlistEntry(symbol="SPY", active=True),   # dup of static -> not added twice
            WatchlistEntry(symbol="OLD", active=False),  # inactive -> excluded
        ])
        s.commit()
        out = effective_symbols(s, _settings("SPY,QQQ"))
    assert out == ["SPY", "QQQ", "NVDA"]  # static first, then new active research ticker


def test_effective_symbols_falls_back_when_table_missing():
    # No watchlist_entries table created -> graceful fallback to static list.
    engine = create_engine("sqlite://")
    with Session(engine) as s:
        assert research_symbols(s) == []
        out = effective_symbols(s, _settings("SPY,QQQ"))
    assert out == ["SPY", "QQQ"]
