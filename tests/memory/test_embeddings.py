"""Tests for embedding persistence wiring (SQLite + FakeLLM, no pgvector).

The ``chunks`` table can't be created on SQLite (pgvector ``vector`` + ARRAY
columns), so the actual INSERT is exercised only against real Postgres. Here we
verify (a) the pure vector formatter and (b) that ``ingest_document`` hands the
*full* document text + theme/symbol tags to the embedding seam and treats an
embedding failure as non-fatal.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory import pdf_pipeline
from trading_intel.memory.embeddings import format_vector
from trading_intel.memory.models import Document, Theme, ThemeObservation

TAGS_JSON = (
    '{"summary": "S", "themes": [{"name": "Vol surface", "scope": "macro",'
    ' "sentiment": 0.1, "confidence": 0.7}], "symbols": ["SPX"]}'
)
FULL_TEXT = "Methodology paragraph about dealer gamma and vanna flow. " * 40


class FakeLLM:
    def __init__(self, tags_json=TAGS_JSON):
        self._tags = tags_json

    def complete(self, prompt, *, model=None, max_tokens=2048):
        return self._tags if "JSON object" in prompt else "## Overview\nNotes."

    def chat(self, messages, *, model=None, max_tokens=2048):
        return ""

    def embed(self, text, *, model=None):
        items = [text] if isinstance(text, str) else text
        return [[0.1, 0.2, 0.3] for _ in items]


def _session() -> Session:
    engine = create_engine("sqlite://")
    for table in (Document.__table__, Theme.__table__, ThemeObservation.__table__):
        table.create(engine)
    return Session(engine)


def _pdf(tmp_path: Path) -> Path:
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 sha bytes")
    return f


def test_format_vector():
    assert format_vector([0.1, 0.2, -3]) == "[0.1,0.2,-3.0]"
    assert format_vector([]) == "[]"


def test_ingest_embeds_full_text_with_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: (FULL_TEXT, 2))
    captured = {}

    def fake_embed(
        session, llm, *, document_id, text_body, theme_ids, symbols, obs_date, model=None
    ):
        captured.update(
            document_id=document_id,
            text_body=text_body,
            theme_ids=theme_ids,
            symbols=symbols,
        )
        return 5

    monkeypatch.setattr(pdf_pipeline, "embed_and_store_chunks", fake_embed)

    status = pdf_pipeline.ingest_document(
        _session(), FakeLLM(), _pdf(tmp_path), playbook_dir=tmp_path
    )
    assert status == "ingested"
    # Full document text is embedded (not the 14k playbook clip — here they're equal anyway).
    assert captured["text_body"] == FULL_TEXT
    assert captured["symbols"] == ["SPX"]
    assert len(captured["theme_ids"]) == 1 and captured["theme_ids"][0] is not None


def test_ingest_embed_failure_is_non_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: (FULL_TEXT, 2))

    def boom(*a, **k):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(pdf_pipeline, "embed_and_store_chunks", boom)
    session = _session()
    status = pdf_pipeline.ingest_document(session, FakeLLM(), _pdf(tmp_path), playbook_dir=tmp_path)
    assert status == "ingested"
    # Document + theme still committed despite the embedding failure.
    assert session.execute(select(Document)).scalar_one() is not None
    assert session.execute(select(Theme)).scalar_one().name == "Vol surface"


def test_ingest_no_embed_flag_skips_embedding(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_pipeline, "extract_text", lambda _p: (FULL_TEXT, 2))

    def fail_if_called(*a, **k):
        raise AssertionError("embed_and_store_chunks must not be called when embed=False")

    monkeypatch.setattr(pdf_pipeline, "embed_and_store_chunks", fail_if_called)
    status = pdf_pipeline.ingest_document(
        _session(), FakeLLM(), _pdf(tmp_path), playbook_dir=tmp_path, embed=False
    )
    assert status == "ingested"
