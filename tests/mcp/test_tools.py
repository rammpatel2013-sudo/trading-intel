"""Unit tests for the MCP tool wrappers.

Mirror the existing ``tests/synthesis/test_am_summary.py`` pattern: SQLite
in-memory ``Session``, a ``StubLLM`` matching the ``LLMProvider`` Protocol,
and a freshly minted ``Settings``. The MCP tools are pure functions — no
FastMCP involved here.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.mcp import tools
from trading_intel.memory.models import AmSummary, WatchlistEntry

_TABLES = (AmSummary, WatchlistEntry)


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
