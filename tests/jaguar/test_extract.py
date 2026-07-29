"""Ollama condenser — fake completer + fallback, no real Ollama needed."""

from __future__ import annotations

from trading_intel.jaguar.extract import condense, first_sentences

NOTE = (
    "Boston Scientific reports Wednesday. Someone bought 12,000 December 50 calls at "
    "$4.48, mostly new risk. The buyer is right about the company and possibly early "
    "on the calendar. A name that can re-rate quickly if results are good."
)


class _FakeLLM:
    def __init__(self, resp: str) -> None:
        self.resp = resp
        self.prompt: str | None = None

    def complete(self, prompt, *, model=None, max_tokens=2048):
        self.prompt = prompt
        return self.resp


class _BoomLLM:
    def complete(self, prompt, *, model=None, max_tokens=2048):
        raise RuntimeError("ollama down")


def test_condense_uses_llm_output():
    llm = _FakeLLM("Follow the December-50 buyer; right company, maybe early.")
    out = condense(NOTE, llm=llm, ticker="BSX")
    assert out == "Follow the December-50 buyer; right company, maybe early."
    assert "BSX" in (llm.prompt or "") and "invent nothing" in (llm.prompt or "")


def test_condense_falls_back_on_llm_error():
    out = condense(NOTE, llm=_BoomLLM(), ticker="BSX", sentences=2)
    assert out == first_sentences(NOTE, 2)
    assert out.startswith("Boston Scientific reports Wednesday.")


def test_condense_empty_is_empty():
    assert condense("", llm=_BoomLLM()) == ""
