"""Tests for the EOD report's knowledge-grounded per-tab summaries.

The pgvector retrieval is monkeypatched so these run without Postgres/Ollama;
they exercise the prompt assembly, metadata capture, graceful degradation, and
the HTML-block builder (CLAUDE.md rule 7 path: local LLMProvider, no cloud).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from trading_intel.memory.retrieval import ChunkHit
from trading_intel.synthesis import eod_knowledge


class StubLLM:
    """LLMProvider stub returning a fixed completion."""

    def __init__(self, text: str = "Grounded analyst note.") -> None:
        self._text = text
        self.calls: list[str] = []

    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        self.calls.append(prompt)
        return self._text

    def chat(self, messages, *, model=None, max_tokens=2048) -> str:
        return self._text

    def embed(self, text, *, model=None):
        return [[0.0]]


class FailingLLM(StubLLM):
    def complete(self, prompt: str, *, model=None, max_tokens=2048) -> str:
        raise RuntimeError("ollama down")


SETTINGS = SimpleNamespace(LLM_DAILY_MODEL="qwen2.5:14b")

_HITS = [
    ChunkHit(chunk_id=1, document_id=9, title="vix-term-structure", text="Contango means...", distance=0.1),
    ChunkHit(chunk_id=2, document_id=9, title="vix-term-structure", text="Backwardation is front-end stress.", distance=0.2),
]


def _patch_retrieval(monkeypatch, hits=None, raises=False):
    def fake(*args, **kwargs):
        if raises:
            raise RuntimeError("pgvector unavailable")
        return list(hits or [])
    monkeypatch.setattr(eod_knowledge, "retrieve_chunks", fake)


def test_summary_succeeds_and_captures_metadata(monkeypatch):
    _patch_retrieval(monkeypatch, _HITS)
    text, meta = eod_knowledge.knowledge_summary(
        None, StubLLM(), SETTINGS, tab="term", as_of="2026-06-12", data="VIX 15.77"
    )
    assert text == "Grounded analyst note."
    assert meta["used_llm"] is True
    assert meta["model"] == "qwen2.5:14b"
    assert meta["sources"] == ["vix-term-structure", "vix-term-structure"]
    assert meta["n_hits"] == 2


def test_prompt_includes_figures_and_kb(monkeypatch):
    _patch_retrieval(monkeypatch, _HITS)
    llm = StubLLM()
    eod_knowledge.knowledge_summary(
        None, llm, SETTINGS, tab="term", as_of="2026-06-12", data="VIX9D 13.41"
    )
    prompt = llm.calls[0]
    assert "VIX9D 13.41" in prompt          # current figures threaded in
    assert "Backwardation is front-end stress." in prompt  # kb grounding threaded in
    assert "NOT a forecast" in prompt       # rule-4 guardrail present


def test_llm_failure_degrades_to_empty(monkeypatch):
    _patch_retrieval(monkeypatch, _HITS)
    text, meta = eod_knowledge.knowledge_summary(
        None, FailingLLM(), SETTINGS, tab="summary", as_of="2026-06-12", data="x"
    )
    assert text == ""
    assert meta["used_llm"] is False
    assert meta["model"] is None


def test_retrieval_failure_still_calls_llm(monkeypatch):
    _patch_retrieval(monkeypatch, raises=True)
    text, meta = eod_knowledge.knowledge_summary(
        None, StubLLM(), SETTINGS, tab="cor", as_of="2026-06-12", data="COR1M 0.42"
    )
    assert text == "Grounded analyst note."  # no kb, but the note still renders
    assert meta["n_hits"] == 0
    assert meta["sources"] == []


def test_build_blocks_omits_empty_and_adds_sources(monkeypatch):
    _patch_retrieval(monkeypatch, _HITS)
    figures = {"term": "VIX 15.77", "cor": "COR1M 0.42"}
    blocks = eod_knowledge.build_knowledge_blocks(
        None, StubLLM(), SETTINGS, as_of="2026-06-12", figures=figures
    )
    assert set(blocks) == {"term", "cor"}
    assert "Knowledge read" in blocks["term"]
    assert "Grounded analyst note." in blocks["term"]
    # deduplicated source titles appear once
    assert blocks["term"].count("vix-term-structure") == 1


def test_build_blocks_skips_when_llm_fails(monkeypatch):
    _patch_retrieval(monkeypatch, _HITS)
    blocks = eod_knowledge.build_knowledge_blocks(
        None, FailingLLM(), SETTINGS, as_of="2026-06-12", figures={"term": "x"}
    )
    assert blocks == {}
