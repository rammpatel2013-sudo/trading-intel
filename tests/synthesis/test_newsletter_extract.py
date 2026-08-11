"""Unit tests for the newsletter levels/scenarios extractor (pure; stub LLM)."""
from __future__ import annotations

from trading_intel.synthesis import newsletter_extract as nx


class _StubLLM:
    """Returns a canned reply regardless of prompt (matches LLMProvider.complete)."""

    def __init__(self, reply: str):
        self._reply = reply

    def complete(self, prompt: str, *, model=None, max_tokens: int = 2048) -> str:
        return self._reply

    def chat(self, messages, *, model=None, max_tokens: int = 2048) -> str:  # pragma: no cover
        return self._reply

    def embed(self, text, *, model=None):  # pragma: no cover
        return [[0.0]]


_GOOD = """Here you go:
```json
{
  "levels": [
    {"name": "Gamma Flip", "value": "6,350", "unit": "SPX", "note": "dealers flip short below"},
    {"name": "call_wall", "value": 6400, "unit": "SPX", "note": null},
    {"name": "expected_move", "value": "0.6%", "unit": "percent", "note": null},
    {"name": "junk", "value": "not-a-number", "unit": "SPX", "note": null}
  ],
  "scenarios": [
    {"trigger": "SPX holds above 6350", "consequence": "grind to 6400 call wall", "direction": "bullish", "confidence": "high"},
    {"trigger": "loses 6300", "consequence": "air pocket to 6250", "direction": "bearish", "confidence": "medium"},
    {"consequence": "no trigger given", "direction": "bogus"}
  ],
  "one_line": "Constructive above the gamma flip."
}
```"""


def test_extract_parses_levels_and_scenarios():
    r = nx.extract_newsletter("DOC", "some body text", _StubLLM(_GOOD))
    # the junk (non-numeric) level is dropped; the other three survive
    names = {lv["name"] for lv in r.levels}
    assert names == {"gamma_flip", "call_wall", "expected_move"}
    flip = next(lv for lv in r.levels if lv["name"] == "gamma_flip")
    assert flip["value"] == 6350.0 and flip["unit"] == "SPX"  # "6,350" coerced
    em = next(lv for lv in r.levels if lv["name"] == "expected_move")
    assert em["value"] == 0.6  # "%" stripped
    # the scenario with no trigger is dropped; bad direction/confidence → None
    assert len(r.scenarios) == 2
    assert r.scenarios[0]["direction"] == "bullish"
    assert r.scenarios[0]["confidence"] == "high"
    assert r.one_line and "gamma flip" in r.one_line.lower()
    assert not r.empty


def test_empty_body_returns_empty():
    r = nx.extract_newsletter("NORSEMAN", "", _StubLLM(_GOOD))
    assert r.empty and r.levels == [] and r.scenarios == []


def test_unparseable_reply_returns_empty():
    r = nx.extract_newsletter("KURT", "body", _StubLLM("sorry, I can't do that"))
    assert r.empty


def test_bad_unit_and_direction_are_nulled():
    reply = '{"levels":[{"name":"x","value":10,"unit":"bananas"}],' \
            '"scenarios":[{"trigger":"t","direction":"sideways","confidence":"vibes"}]}'
    r = nx.extract_newsletter("VOLSIGNALS", "b", _StubLLM(reply))
    assert r.levels[0]["unit"] is None
    assert r.scenarios[0]["direction"] is None and r.scenarios[0]["confidence"] is None


def test_caps_item_counts():
    lots = ",".join('{"name":"n%d","value":%d}' % (i, i) for i in range(30))
    r = nx.extract_newsletter("DOC", "b", _StubLLM('{"levels":[' + lots + '],"scenarios":[]}'))
    assert len(r.levels) == nx._MAX_ITEMS
