"""Tests for the LLM watchlist extractor (fake provider, no Ollama)."""

from __future__ import annotations

from trading_intel.synthesis.watchlist_extract import extract_watchlist, parse_candidates


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, prompt: str, *, model=None, max_tokens: int = 2048) -> str:
        return self._reply

    def chat(self, messages, *, model=None, max_tokens: int = 2048) -> str:
        return self._reply

    def embed(self, text, *, model=None):
        return [[0.0]]


_GOOD = '''Here you go:
{"tickers": [
  {"symbol": "nvda", "rationale": "AI accelerator demand", "sentiment": 1.4,
   "confidence": 0.9, "themes": ["AI capex", "data center"]},
  {"symbol": "AAPL", "rationale": "services margin", "sentiment": -0.2,
   "confidence": 1.5, "themes": []},
  {"symbol": "NVDA", "rationale": "dup should be dropped", "sentiment": 0.5},
  {"symbol": "not a ticker", "rationale": "ignore"}
]}'''


def test_parse_candidates_clamps_dedupes_validates():
    out = parse_candidates(_GOOD)
    syms = [c.symbol for c in out]
    assert syms == ["NVDA", "AAPL"]  # uppercased, deduped, junk symbol dropped
    nvda = out[0]
    assert nvda.sentiment == 1.0  # clamped from 1.4
    assert nvda.themes == ["AI capex", "data center"]
    aapl = out[1]
    assert aapl.confidence == 1.0  # clamped from 1.5
    assert aapl.themes == []


def test_parse_candidates_bad_json():
    assert parse_candidates("no json here") == []
    assert parse_candidates('{"tickers": "notalist"}') == []


def test_extract_watchlist_uses_llm():
    out = extract_watchlist(FakeLLM(_GOOD), "Some Note", "body text")
    assert [c.symbol for c in out] == ["NVDA", "AAPL"]
