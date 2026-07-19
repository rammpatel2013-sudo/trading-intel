"""Tests for the LLM inflection extraction — pure + fake-LLM, no Ollama needed."""

from __future__ import annotations

from trading_intel.earnings.extract import (
    candidate_sentences,
    extract_inflection,
    parse_selection,
)

TEXT = (
    "Demand accelerated and revenue growth was strong this quarter. "
    "We are raising our guidance for the full year. "
    "However margins declined amid pricing pressure and weakness. "
    "Macro headwinds created uncertainty in the outlook."
)


class _FakeLLM:
    def __init__(self, resp: str) -> None:
        self.resp = resp
        self.prompt: str | None = None

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        self.prompt = prompt
        return self.resp


class _BoomLLM:
    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str:
        raise RuntimeError("ollama down")


def test_candidate_sentences_buckets():
    pos, neg = candidate_sentences(TEXT)
    assert "Demand accelerated" in pos[0]  # highest-scoring positive
    assert any("declined" in s for s in neg)
    assert all("declined" not in s for s in pos)


def test_parse_selection_maps_ids():
    pos, neg = ["A", "B", "C"], ["X", "Y", "Z"]
    pq, nq, summary = parse_selection(
        "TOP_POSITIVE: P1, P3\nTOP_NEGATIVE: N2\nSUMMARY: demand improved", pos, neg
    )
    assert pq == ["A", "C"]
    assert nq == ["Y"]
    assert summary == "demand improved"


def test_parse_selection_handles_none_and_out_of_range():
    pq, nq, summary = parse_selection(
        "TOP_POSITIVE: NONE\nTOP_NEGATIVE: P9\nSUMMARY:", ["A"], ["X"]
    )
    assert pq == [] and nq == []
    assert summary == ""


def test_extract_with_fake_llm_selects_verbatim():
    llm = _FakeLLM("TOP_POSITIVE: P1\nTOP_NEGATIVE: N1\nSUMMARY: demand accelerated but margins fell")
    d = extract_inflection("X", TEXT, None, llm=llm, model="qwen2.5:14b")
    assert d.used_llm is True
    assert d.model == "qwen2.5:14b"
    assert d.positive_quotes and "Demand accelerated" in d.positive_quotes[0]
    assert d.negative_quotes and "declined" in d.negative_quotes[0]
    assert "margins fell" in d.summary
    assert "POSITIVE candidates" in (llm.prompt or "")  # grounded prompt was built


def test_extract_degrades_on_llm_failure():
    d = extract_inflection("X", TEXT, None, llm=_BoomLLM())
    assert d.used_llm is False
    assert d.summary == ""
    assert d.positive_quotes and "Demand accelerated" in d.positive_quotes[0]  # lexicon fallback
