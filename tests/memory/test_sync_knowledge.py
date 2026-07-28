"""Tests for folder sync + re-indexing (SQLite; chunk ops monkeypatched)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.memory import pdf_pipeline, sync_knowledge, watchlist_ingest
from trading_intel.memory.models import Document, Theme, ThemeObservation, WatchlistEntry

TAGS_JSON = (
    '{"summary": "S", "themes": [{"name": "Gamma", "scope": "macro",'
    ' "sentiment": 0.0, "confidence": 0.5}], "symbols": ["SPX"]}'
)
WL_JSON = (
    '{"tickers": [{"symbol": "NVDA", "rationale": "AI", "sentiment": 0.7, "confidence": 0.8}]}'
)


class FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    def complete(self, prompt, *, model=None, max_tokens=2048):
        # tagging prompts ask for a JSON object; framework prompts don't.
        return self._reply if "JSON object" in prompt else "## Overview\nNotes."

    def chat(self, messages, *, model=None, max_tokens=2048):
        return self._reply

    def embed(self, text, *, model=None):
        items = [text] if isinstance(text, str) else text
        return [[0.1, 0.2, 0.3] for _ in items]


def _sync_meth(session, llm, folder, **kw):
    # Playbooks MUST land outside the scanned folder: .md is now a supported ingest
    # type (investor letters), so writing playbooks into research_dir would make the
    # next sync re-ingest the pipeline's own .md output. Prod keeps them separate too
    # (research/doc vs docs/playbooks). A subdir is safe — discovery is non-recursive.
    kw.setdefault("playbook_dir", folder / "playbooks")
    return sync_knowledge.sync_methodology(session, llm, research_dir=folder, **kw)


def _count(session, model) -> int:
    return session.execute(select(func.count()).select_from(model)).scalar_one()


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for t in (Document.__table__, Theme.__table__, ThemeObservation.__table__,
              WatchlistEntry.__table__):
        t.create(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture(autouse=True)
def _no_pgvector(monkeypatch):
    """Keep the pgvector chunks table out of SQLite by stubbing the seam."""
    calls = {"delete": [], "embed": 0}
    monkeypatch.setattr(sync_knowledge, "delete_chunks", lambda s, did: calls["delete"].append(did))
    monkeypatch.setattr(sync_knowledge, "count_chunks", lambda s, did: 5)  # default: has chunks

    def fake_embed(*a, **k):
        calls["embed"] += 1
        return 3

    monkeypatch.setattr(pdf_pipeline, "embed_and_store_chunks", fake_embed)
    monkeypatch.setattr(sync_knowledge, "embed_and_store_chunks", fake_embed)
    return calls


def _write(folder: Path, name: str, body: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_bytes(body)
    return p


def test_new_file_is_ingested(session, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: ("gamma " * 60, 2))
    _write(tmp_path, "a.pdf", b"%PDF-A")
    stats = _sync_meth(session, FakeLLM(TAGS_JSON), tmp_path)
    assert stats["new"] == 1
    assert _count(session, Document) == 1


def test_unchanged_file_is_skipped(session, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: ("gamma " * 60, 2))
    _write(tmp_path, "a.pdf", b"%PDF-A")
    llm = FakeLLM(TAGS_JSON)
    _sync_meth(session, llm, tmp_path)
    stats = _sync_meth(session, llm, tmp_path)
    assert stats["unchanged"] == 1 and stats["new"] == 0
    assert _count(session, Document) == 1


def test_unchanged_backfills_when_no_chunks(session, tmp_path, monkeypatch, _no_pgvector):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: ("gamma " * 60, 2))
    monkeypatch.setattr(sync_knowledge, "extract_text", lambda _p: ("gamma " * 60, 2))
    monkeypatch.setattr(sync_knowledge, "count_chunks", lambda s, did: 0)  # no chunks yet
    _write(tmp_path, "a.pdf", b"%PDF-A")
    llm = FakeLLM(TAGS_JSON)
    _sync_meth(session, llm, tmp_path)
    embeds_before = _no_pgvector["embed"]
    stats = _sync_meth(session, llm, tmp_path)
    assert stats["backfilled"] == 1
    assert _no_pgvector["embed"] == embeds_before + 1  # backfill embedded once


def test_edited_file_supersedes_old(session, tmp_path, monkeypatch, _no_pgvector):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: ("gamma " * 60, 2))
    path = _write(tmp_path, "a.pdf", b"%PDF-A")
    llm = FakeLLM(TAGS_JSON)
    _sync_meth(session, llm, tmp_path)
    old_id = session.execute(select(Document.id)).scalar_one()

    edited = b"%PDF-A-EDITED"
    path.write_bytes(edited)  # same path, new hash
    stats = _sync_meth(session, llm, tmp_path)
    assert stats["updated"] == 1
    # Exactly one document remains, carrying the EDITED content's hash.
    # (SQLite recycles the deleted row's id, so compare hashes, not ids.)
    docs = session.execute(select(Document)).scalars().all()
    assert len(docs) == 1
    assert docs[0].sha256 == hashlib.sha256(edited).hexdigest()
    assert old_id in _no_pgvector["delete"]  # old chunks were deleted


def test_prune_removed_is_opt_in(session, tmp_path, monkeypatch, _no_pgvector):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: ("gamma " * 60, 2))
    path = _write(tmp_path, "a.pdf", b"%PDF-A")
    llm = FakeLLM(TAGS_JSON)
    _sync_meth(session, llm, tmp_path)
    path.unlink()  # file removed from the folder

    # Default: do NOT prune.
    stats = _sync_meth(session, llm, tmp_path)
    assert stats["pruned"] == 0
    assert _count(session, Document) == 1

    # Opt-in prune removes it.
    stats = _sync_meth(session, llm, tmp_path, prune_removed=True)
    assert stats["pruned"] == 1
    assert _count(session, Document) == 0


def test_research_sync_new_and_supersede(session, tmp_path, monkeypatch):
    monkeypatch.setattr(watchlist_ingest, "extract_text", lambda _p: ("NVDA thesis " * 30, 1))
    path = _write(tmp_path, "co.pdf", b"%PDF-CO")
    llm = FakeLLM(WL_JSON)
    out = sync_knowledge.sync_research(session, llm, research_dir=tmp_path)
    assert out["new"] == 1 and out["new_symbols"] == ["NVDA"]
    assert _count(session, WatchlistEntry) == 1

    path.write_bytes(b"%PDF-CO-EDITED")
    out = sync_knowledge.sync_research(session, llm, research_dir=tmp_path)
    assert out["updated"] == 1
    # Superseded: still one document + one entry (old entry replaced).
    assert _count(session, Document) == 1
    assert _count(session, WatchlistEntry) == 1
