"""Unit tests for the MCP tool wrappers.

Mirror the existing ``tests/synthesis/test_am_summary.py`` pattern: SQLite
in-memory ``Session``, a ``StubLLM`` matching the ``LLMProvider`` Protocol,
and a freshly minted ``Settings``. The MCP tools are pure functions — no
FastMCP involved here.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.mcp import tools
from trading_intel.memory.models import (
    AmSummary,
    GreeksSnapshot,
    QuoteDaily,
    WatchlistEntry,
)

_TABLES = (AmSummary, WatchlistEntry, GreeksSnapshot, QuoteDaily)


class StubLLM:
    """LLMProvider stub — matches the Protocol surface used by the tools."""

    def __init__(self, text: str = "Stub narrative.") -> None:
        self._text = text

    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        return self._text

    def chat(self, messages, *, model=None, max_tokens=2048) -> str:
        return self._text

    def embed(self, text, *, model=None):
        if isinstance(text, list):
            return [[0.0] for _ in text]
        return [[0.0]]


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com",
        CONVEX_PASSWORD="x",
        FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY,QQQ",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in _TABLES:
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_get_latest_am_summary_when_empty(session: Session) -> None:
    result = tools.get_latest_am_summary(session)
    assert result == {"date": None, "markdown": None, "metadata": None, "found": False}


def test_get_latest_am_summary_returns_newest(session: Session) -> None:
    session.add(AmSummary(date=date(2026, 5, 30), markdown="old", metadata_json={"v": 1}))
    session.add(AmSummary(date=date(2026, 6, 1), markdown="new", metadata_json={"v": 2}))
    session.commit()

    result = tools.get_latest_am_summary(session)
    assert result["found"] is True
    assert result["date"] == "2026-06-01"
    assert result["markdown"] == "new"
    assert result["metadata"] == {"v": 2}


def test_get_am_summary_by_date_hit(session: Session) -> None:
    session.add(AmSummary(date=date(2026, 6, 1), markdown="x", metadata_json={"k": "v"}))
    session.commit()

    result = tools.get_am_summary_by_date(session, "2026-06-01")
    assert result["found"] is True
    assert result["markdown"] == "x"
    assert result["metadata"] == {"k": "v"}


def test_get_am_summary_by_date_miss(session: Session) -> None:
    result = tools.get_am_summary_by_date(session, "2026-06-01")
    assert result == {"date": "2026-06-01", "found": False, "markdown": None}


def test_get_am_summary_by_date_invalid_iso(session: Session) -> None:
    result = tools.get_am_summary_by_date(session, "not-a-date")
    assert result["found"] is False
    assert "invalid date" in result["error"]


def test_list_am_summary_dates(session: Session) -> None:
    for d in (date(2026, 6, 1), date(2026, 5, 31), date(2026, 5, 30)):
        session.add(AmSummary(date=d, markdown="x", metadata_json={}))
    session.commit()

    result = tools.list_am_summary_dates(session, limit=2)
    assert result["count"] == 2
    assert result["dates"] == ["2026-06-01", "2026-05-31"]


def test_list_am_summary_dates_clamps_limit(session: Session) -> None:
    # Should silently clamp to >=1 and <=365 rather than raising
    result = tools.list_am_summary_dates(session, limit=0)
    assert result["count"] == 0  # nothing in DB, but call must not crash


def test_normalise_symbols_uppercases_and_dedupes(session: Session) -> None:
    settings = _settings()
    out = tools._normalise_symbols(session, ["aapl", "MSFT", "aapl", "  nvda "], settings)
    assert out == ["AAPL", "MSFT", "NVDA"]


def test_normalise_symbols_defaults_to_effective_watchlist(session: Session) -> None:
    settings = _settings(WATCHLIST="SPY,QQQ")
    out = tools._normalise_symbols(session, None, settings)
    # WatchlistEntry table is empty so research_symbols returns [] -> static only
    assert out == ["SPY", "QQQ"]


def test_search_knowledge_rejects_bad_kind(session: Session) -> None:
    result = tools.search_knowledge(session, StubLLM(), "anything", kind="bogus")
    assert result["hits"] == []
    assert "invalid kind" in result["error"]


def test_search_knowledge_clamps_k() -> None:
    # k beyond the cap should be silently clamped — we test the validation
    # branch without touching pgvector by stubbing retrieve_chunks.
    from trading_intel.mcp import tools as tools_module

    captured: dict = {}

    def fake_retrieve(*args, **kwargs):
        captured.update(kwargs)
        return []

    original = tools_module.retrieve_chunks
    tools_module.retrieve_chunks = fake_retrieve  # type: ignore[assignment]
    try:
        result = tools_module.search_knowledge(
            session=None,  # not used by the fake
            llm=StubLLM(),
            query="vol regime",
            kind="methodology",
            k=999,
        )
    finally:
        tools_module.retrieve_chunks = original  # type: ignore[assignment]

    assert result["k"] == 20  # _MAX_K
    assert captured["k"] == 20
    assert captured["kind"] == "methodology"


def test_get_gamma_history_empty(session: Session) -> None:
    result = tools.get_gamma_history(session, "NVDA", days=30)
    assert result == {"symbol": "NVDA", "rows": [], "count": 0, "found": False}


def test_get_gamma_history_series_and_summary(session: Session) -> None:
    # three daily snapshots, rising net GEX, spot above flip (long gamma)
    for i, gex in enumerate((100.0, 200.0, 300.0)):
        session.add(
            GreeksSnapshot(
                symbol="NVDA", ts=datetime(2026, 6, 1 + i, 6, 45),
                spot=230.0, gex_total=gex, gex_flip=210.0, atm_iv=0.6,
            )
        )
    session.commit()

    result = tools.get_gamma_history(session, "nvda", days=30)
    assert result["found"] is True
    assert result["count"] == 3
    assert result["summary"]["current_gex"] == 300.0
    assert result["summary"]["start_gex"] == 100.0
    assert result["summary"]["direction"] == "up"
    assert "long gamma" in result["rows"][-1]["regime"]


def test_get_technicals_empty(session: Session) -> None:
    result = tools.get_technicals(session, "NVDA", days=120)
    assert result == {"symbol": "NVDA", "found": False, "indicators": None}


def test_get_technicals_computes_pure_pandas_indicators(session: Session) -> None:
    # 30 ascending bars -> RSI/SMA/EMA computable without the 'ta' library
    for i in range(30):
        px = 100.0 + i
        session.add(
            QuoteDaily(
                symbol="NVDA", date=date(2026, 5, 1) + timedelta(days=i),
                open=px - 0.5, high=px + 1.0, low=px - 1.0, close=px, volume=1_000 + i,
            )
        )
    session.commit()

    result = tools.get_technicals(session, "NVDA", days=120)
    assert result["found"] is True
    assert result["bars"] == 30
    assert result["indicators"]["rsi14"] is not None
    assert result["indicators"]["sma20"] is not None
    assert isinstance(result["candlestick_patterns"], list)


class _StubSource:
    """Minimal OptionsDataSource stand-in returning a fixed tape frame."""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def time_and_sales(self, symbol: str, *, limit: int = 200, day: int = 0) -> pd.DataFrame:
        return self._df


def test_get_time_and_sales_summarizes_live_prints() -> None:
    df = pd.DataFrame(
        {
            "time": [pd.Timestamp("2026-06-02 15:30")],
            "opt_kind": ["call"], "strike": [230.0],
            "expiration": [pd.Timestamp("2026-06-19")],
            "price": [2.5], "size": [100.0], "premium": [250000.0],
            "aggressor_side": ["buy"], "iv": [0.6], "delta": [0.5], "spot": [228.0],
        }
    )
    out = tools.get_time_and_sales(_StubSource(df), "nvda", limit=10)
    assert out["found"] is True
    assert out["live_prints"] is True
    assert out["rows"][0]["premium"] == 250000.0
    assert out["rows"][0]["side"] == "buy"


def test_get_time_and_sales_flags_after_hours_zeros() -> None:
    df = pd.DataFrame(
        {"opt_kind": ["call"], "strike": [230.0], "premium": [0.0], "size": [0.0]}
    )
    out = tools.get_time_and_sales(_StubSource(df), "NVDA")
    assert out["live_prints"] is False
    assert "after-hours" in out["note"]
