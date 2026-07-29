"""Local-LLM condensing of Fahad's prose (rule 7 — Ollama only, no cloud LLM).

The deterministic :mod:`jaguar.parse` layer already grounds every ticker, contract and
size, so this layer never *extracts* facts — it only condenses the analyst's reasoning
to the most important 2-3 sentences for the brief. The prompt forbids inventing anything
not in the note, and every call degrades to a verbatim sentence excerpt when Ollama is
down or missing, so the brief always renders. Tested with a fake completer + the fallback.
"""

from __future__ import annotations

import re
from typing import Protocol

_SENT = re.compile(r"(?<=[.!?])\s+")

_PROMPT = (
    "You are condensing an options analyst's note{sym}. In {n} sentences or fewer, "
    "capture the single most important part of HIS reasoning — the thesis and why the "
    "flow or catalyst matters. Use ONLY facts, tickers and numbers already in the note; "
    "invent nothing, add no preamble.\n\nNote:\n{text}\n\nCondensed:"
)


class Completer(Protocol):
    """Minimal LLM contract (satisfied by ``synthesis.llm.LLMProvider``)."""

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str: ...


def first_sentences(text: str, n: int = 2) -> str:
    """The first ``n`` sentences of ``text`` (the grounded fallback)."""
    parts = _SENT.split(text.strip())
    return " ".join(p.strip() for p in parts[:n]).strip()


def condense(
    text: str,
    *,
    llm: Completer,
    ticker: str | None = None,
    model: str | None = None,
    max_tokens: int = 180,
    sentences: int = 3,
) -> str:
    """Condense ``text`` to <= ``sentences`` sentences; degrade to a verbatim excerpt.

    Never raises — Ollama being down/missing just yields the deterministic excerpt.
    """
    text = (text or "").strip()
    if not text:
        return ""
    prompt = _PROMPT.format(sym=f" on {ticker}" if ticker else "", n=sentences, text=text[:2400])
    try:
        out = llm.complete(prompt, model=model, max_tokens=max_tokens).strip()
    except Exception:
        out = ""
    return out or first_sentences(text, sentences)
