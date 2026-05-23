"""Tests for research->watchlist ingest (SQLite, fake LLM, no Ollama/PDF)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.memory import watchlist_ingest
from trading_intel.memory.models import Document, WatchlistEntry


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, prompt: str, *, model=None, max_tokens: int = 2048) -> str:
        return self._reply

    def chat(self, messages, *, model=None, max_tokens: int = 2048) -> str:
        return self._reply

    def embed(self, text, *, model=None):
        return [[0.0]]


_REPLY = (
    '{"tickers": ['
    '{"symbol": "NVDA", "rationale": "AI demand", "sentiment": 0.8, "confidence": 0.9, '
    '"themes": ["AI capex"]}, '
    '{"symbol": "AMD", "rationale": "share gains"}]}'
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Document.__table__.create(engine)
    WatchlistEntry.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _make_file(tmp_path: Path) -> Path:
    f = tmp_path / "acme_research.pdf"
    f.write_bytes(b"%PDF-1.4 fake bytes for sha")
    return f


def test_ingest_creates_entries(session: Session, tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist_ingest, "extract_text", lambda _p: ("NVDA and AMD " * 20, 3))
    path = _make_file(tmp_path)
    result = watchlist_ingest.ingest_research(session, FakeLLM(_REPLY), path)
    assert result["status"] == "ingested"
    assert set(result["symbols"]) == {"NVDA", "AMD"}
    entries = list(session.execute(select(WatchlistEntry)).scalars())
    assert {e.symbol for e in entries} == {"NVDA", "AMD"}
    nvda = next(e for e in entries if e.symbol == "NVDA")
    assert nvda.sentiment == pytest.approx(0.8)
    assert nvda.themes == ["AI capex"]
    assert nvda.source_doc_id is not None


def test_ingest_is_idempotent_by_sha(session: Session, tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist_ingest, "extract_text", lambda _p: ("NVDA and AMD " * 20, 3))
    path = _make_file(tmp_path)
    watchlist_ingest.ingest_research(session, FakeLLM(_REPLY), path)
    n1 = session.execute(select(func.count()).select_from(WatchlistEntry)).scalar_one()
    again = watchlist_ingest.ingest_research(session, FakeLLM(_REPLY), path)
    assert again["status"] == "skipped"
    n2 = session.execute(select(func.count()).select_from(WatchlistEntry)).scalar_one()
    assert n1 == n2


def test_ingest_empty_text(session: Session, tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist_ingest, "extract_text", lambda _p: ("  ", 0))
    result = watchlist_ingest.ingest_research(session, FakeLLM(_REPLY), _make_file(tmp_path))
    assert result["status"] == "empty"
    assert session.execute(select(func.count()).select_from(WatchlistEntry)).scalar_one() == 0


def test_ingest_folder_collects_new_symbols(session: Session, tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist_ingest, "extract_text", lambda _p: ("NVDA and AMD " * 20, 3))
    # Two files in the drop folder.
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4 file a")
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4 file b")
    out = watchlist_ingest.ingest_folder(session, FakeLLM(_REPLY), research_dir=tmp_path)
    assert out["ingested"] == 2
    assert set(out["new_symbols"]) == {"NVDA", "AMD"}


def test_ingest_folder_missing_dir(session: Session, tmp_path):
    out = watchlist_ingest.ingest_folder(session, FakeLLM(_REPLY), research_dir=tmp_path / "nope")
    assert out["ingested"] == 0 and out["new_symbols"] == []
