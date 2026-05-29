"""Tests for the AM-report scheduled job — SQLite, stub LLM."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import (
    AmSummary,
    FlowSnapshot,
    GreeksChain,
    GreeksSnapshot,
    IndexSkewDaily,
    IntradayFlow,
    SkewSnapshot,
    WatchlistEntry,
)
from trading_intel.scheduler.jobs import am_summary

_TABLES = (
    AmSummary, GreeksSnapshot, GreeksChain, WatchlistEntry,
    FlowSnapshot, IntradayFlow, IndexSkewDaily, SkewSnapshot,
)


class StubLLM:
    def __init__(self, text: str = "Stub narrative.") -> None:
        self._text = text

    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        return self._text

    def chat(self, messages, *, model=None, max_tokens=2048) -> str:
        return self._text

    def embed(self, text, *, model=None):
        return [[0.0]]


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com",
        CONVEX_PASSWORD="x",
        FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY",
        LLM_DAILY_MODEL="qwen2.5:3b",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in _TABLES:
        tbl.__table__.create(engine)
    with Session(engine) as s:
        s.add(
            GreeksSnapshot(
                symbol="SPY", ts=datetime(2026, 5, 23, 6, 45), spot=735.0,
                gex_total=1.5e7, gex_flip=728.0, atm_iv=0.19, source="convex",
            )
        )
        s.commit()
        yield s


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(AmSummary)).scalar_one()


def test_run_writes_one_row(session: Session):
    am_summary.run(session, StubLLM("Morning note."), settings=_settings())
    row = session.execute(select(AmSummary)).scalar_one()
    assert _count(session) == 1
    assert "Morning note." in row.markdown
    assert row.claude_model == "qwen2.5:3b"
    assert row.metadata_json["used_llm"] is True


def test_run_is_idempotent_and_refreshes(session: Session):
    s = _settings()
    am_summary.run(session, StubLLM("first body"), settings=s)
    assert _count(session) == 1
    am_summary.run(session, StubLLM("second body"), settings=s)
    assert _count(session) == 1
    row = session.execute(select(AmSummary)).scalar_one()
    assert "second body" in row.markdown


def test_run_uses_fallback_when_llm_down(session: Session):
    class Down(StubLLM):
        def complete(self, prompt, *, model=None, max_tokens=2048):
            raise RuntimeError("ollama down")

    am_summary.run(session, Down(), settings=_settings())
    row = session.execute(select(AmSummary)).scalar_one()
    assert row.claude_model is None
    assert row.metadata_json["used_llm"] is False
    assert "deterministic regime tables" in row.markdown


def test_run_picks_up_research_ticker(session: Session):
    session.add(
        WatchlistEntry(
            symbol="NVDA", source_doc_id=None, rationale="AI capex",
            sentiment=0.5, confidence=0.7, themes=["AI"],
            added_at=datetime(2026, 5, 23, 5, 0), active=True,
        )
    )
    session.commit()
    am_summary.run(session, StubLLM(), settings=_settings())
    row = session.execute(select(AmSummary)).scalar_one()
    assert "NVDA" in row.markdown
    assert "NVDA" in row.metadata_json["research_symbols"]
