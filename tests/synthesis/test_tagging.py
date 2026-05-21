"""Tests for LLM tagging/extraction parsing — no Ollama, no network."""

from __future__ import annotations

from trading_intel.synthesis.tagging import (
    DocTags,
    _parse_tags,
    extract_framework,
    tag_document,
)

GOOD = (
    '{"summary": "A primer on volatility trading.",'
    ' "themes": [{"name": "Volatility risk premium", "scope": "macro",'
    ' "sentiment": 0.2, "confidence": 0.8}],'
    ' "symbols": ["spy", "vix"]}'
)


class FakeLLM:
    def __init__(self, response=""):
        self.response = response
        self.calls = []

    def complete(self, prompt, *, model=None, max_tokens=2048):
        self.calls.append((model, prompt))
        return self.response

    def chat(self, messages, *, model=None, max_tokens=2048):
        return self.response

    def embed(self, text, *, model=None):
        return [[0.0]]


def test_tag_document_parses_clean_json():
    tags = tag_document(FakeLLM(GOOD), "Trading Volatility", "body text")
    assert tags.summary.startswith("A primer")
    assert len(tags.themes) == 1
    theme = tags.themes[0]
    assert theme.name == "Volatility risk premium"
    assert theme.scope == "macro"
    assert theme.sentiment == 0.2
    assert theme.confidence == 0.8
    assert tags.symbols == ["SPY", "VIX"]  # upper-cased


def test_tag_document_json_wrapped_in_prose():
    noisy = "Sure! Here is the JSON:\n" + GOOD + "\nHope that helps."
    tags = tag_document(FakeLLM(noisy), "x", "y")
    assert len(tags.themes) == 1


def test_tag_document_bad_json_returns_empty():
    assert tag_document(FakeLLM("not json at all"), "x", "y") == DocTags()


def test_parse_tags_clamps_and_defaults_scope():
    raw = (
        '{"summary":"s","themes":[{"name":"T","scope":"weird",'
        '"sentiment":5,"confidence":-2}],"symbols":[]}'
    )
    theme = _parse_tags(raw).themes[0]
    assert theme.scope == "macro"  # invalid scope -> default
    assert theme.sentiment == 1.0  # clamped to [-1, 1]
    assert theme.confidence == 0.0  # clamped to [0, 1]


def test_parse_tags_skips_nameless_and_nondict():
    raw = '{"themes":[{"scope":"macro"}, "junk", {"name":"Real","scope":"sector"}]}'
    tags = _parse_tags(raw)
    assert [t.name for t in tags.themes] == ["Real"]
    assert tags.themes[0].scope == "sector"


def test_extract_framework_strips_and_passes_model():
    llm = FakeLLM("  ## Overview\nstuff\n  ")
    out = extract_framework(llm, "Title", "text", model="qwen2.5:3b")
    assert out == "## Overview\nstuff"
    assert llm.calls[0][0] == "qwen2.5:3b"
