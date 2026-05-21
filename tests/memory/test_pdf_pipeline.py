"""Tests for the research knowledge pipeline.

Orchestration runs against an in-memory SQLite DB (only the 3 tables the
pipeline touches) with a FakeLLM, so it needs neither Postgres nor Ollama.
Real-file extraction is checked against the committed research/ samples and
skips cleanly if they are absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Document, Theme, ThemeObservation
from trading_intel.memory.pdf_pipeline import (
    discover_documents,
    extract_text,
    ingest_document,
    slugify,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_PDF = REPO_ROOT / "research" / "ManagingSmileRisk.pdf"
SAMPLE_DOCX = REPO_ROOT / "research" / "gex_explanation.docx"

TAGS_JSON = (
    '{"summary": "S",'
    ' "themes": [{"name": "Vol surface", "scope": "macro",'
    ' "sentiment": 0.1, "confidence": 0.7}],'
    ' "symbols": ["SPX"]}'
)


class FakeLLM:
    """Returns tagging JSON for the tagging prompt, framework md otherwise."""

    def __init__(self, framework="## Overview\nNotes.", tags_json=TAGS_JSON):
        self._framework = framework
        self._tags = tags_json

    def complete(self, prompt, *, model=None, max_tokens=2048):
        return self._tags if "JSON object" in prompt else self._framework

    def chat(self, messages, *, model=None, max_tokens=2048):
        return ""

    def embed(self, text, *, model=None):
        return [[0.0]]


def _sqlite_session():
    engine = create_engine("sqlite://")
    for table in (Document.__table__, Theme.__table__, ThemeObservation.__table__):
        table.create(engine)
    return Session(engine)


def test_slugify():
    assert slugify("Managing Smile Risk!.pdf") == "managing-smile-risk-pdf"
    assert slugify("   ") == "doc"


def test_discover_documents_filters_to_supported(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "b.docx").write_bytes(b"PK")
    (tmp_path / "c.png").write_bytes(b"x")
    (tmp_path / "d.txt").write_text("x")
    assert [p.name for p in discover_documents(tmp_path)] == ["a.pdf", "b.docx"]


@pytest.mark.skipif(not SAMPLE_PDF.exists(), reason="research sample not present")
def test_extract_pdf_real_file():
    text, pages = extract_text(SAMPLE_PDF)
    assert pages > 5
    assert "smile" in text.lower()


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="research sample not present")
def test_extract_docx_real_file():
    text, pages = extract_text(SAMPLE_DOCX)
    assert pages == 0
    assert "gamma" in text.lower()


@pytest.mark.skipif(not SAMPLE_DOCX.exists(), reason="research sample not present")
def test_ingest_document_writes_doc_playbook_and_themes(tmp_path):
    session = _sqlite_session()
    llm = FakeLLM()

    status = ingest_document(session, llm, SAMPLE_DOCX, playbook_dir=tmp_path)
    assert status == "ingested"

    doc = session.execute(select(Document)).scalar_one()
    assert doc.source == "internal"
    assert doc.type == "docx"
    assert doc.kind == "methodology"

    playbook = tmp_path / "gex-explanation.md"
    assert playbook.exists()
    assert "## Overview" in playbook.read_text(encoding="utf-8")

    theme = session.execute(select(Theme)).scalar_one()
    assert theme.name == "Vol surface"
    obs = session.execute(select(ThemeObservation)).scalar_one()
    assert obs.symbol == "SPX"
    assert obs.confidence == 0.7

    # Idempotent: a second run dedupes on sha256 and skips.
    assert ingest_document(session, llm, SAMPLE_DOCX, playbook_dir=tmp_path) == "skipped"
