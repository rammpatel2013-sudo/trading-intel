"""Slice 2 — local-LLM extraction of the specific inflection quotes.

The Stage-1 lexicon (``inflection``) says *which* names inflected and which way.
This layer says *where*: it pre-selects candidate sentences with the lexicon, asks
the local LLM (Ollama via the ``LLMProvider`` contract, rule 7 — no cloud LLM) to
pick the ones that mark the positive / negative business inflection **by ID**, and
maps the IDs back to the verbatim transcript sentences. Selecting by ID means the
returned quotes cannot be hallucinated and the prompt stays small enough for a 14B
model.

Pure helpers (candidate selection, prompt, parse) are unit-tested; the orchestration
is tested with a fake completer. Descriptive only (FlashAlpha rule 4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from trading_intel.earnings.inflection import (
    GUIDANCE_DOWN,
    GUIDANCE_UP,
    NEGATIVE,
    POSITIVE,
    InflectionRead,
    read_inflection,
)

_WORD = re.compile(r"[a-z']+")
_SENT = re.compile(r"(?<=[.!?])\s+")
_MAX_SENT_CHARS = 320  # truncate a candidate for the prompt (returned quote stays full)


class Completer(Protocol):
    """Minimal LLM contract used here (satisfied by ``synthesis.llm.LLMProvider``)."""

    def complete(self, prompt: str, *, model: str | None = None, max_tokens: int = 2048) -> str: ...


@dataclass(frozen=True, slots=True)
class InflectionDetail:
    """Stage-1 read + the LLM-selected positive/negative inflection quotes."""

    read: InflectionRead
    positive_quotes: list[str] = field(default_factory=list)
    negative_quotes: list[str] = field(default_factory=list)
    summary: str = ""
    used_llm: bool = False
    model: str | None = None


def candidate_sentences(text: str, k: int = 6) -> tuple[list[str], list[str]]:
    """Top-k most positive and most negative candidate sentences (verbatim).

    Scores each sentence by lexicon polarity, boosted by guidance raise/cut cues.
    Pure — this is the grounded shortlist the LLM chooses from.
    """
    sentences = [s.strip() for s in _SENT.split(text) if len(s.split()) >= 4]
    pos: list[tuple[int, str]] = []
    neg: list[tuple[int, str]] = []
    for s in sentences:
        toks = _WORD.findall(s.lower())
        p = sum(1 for t in toks if t in POSITIVE)
        n = sum(1 for t in toks if t in NEGATIVE)
        low = s.lower()
        score = (p - n) + (2 if any(g in low for g in GUIDANCE_UP) else 0)
        score -= 2 if any(g in low for g in GUIDANCE_DOWN) else 0
        if score > 0:
            pos.append((score, s))
        elif score < 0:
            neg.append((score, s))
    pos.sort(key=lambda x: x[0], reverse=True)
    neg.sort(key=lambda x: x[0])  # most negative first
    return [s for _, s in pos[:k]], [s for _, s in neg[:k]]


def build_prompt(symbol: str, read: InflectionRead, pos: list[str], neg: list[str]) -> str:
    """Grounded ID-selection prompt (the model refers to sentences by P#/N#)."""

    def _lines(letter: str, items: list[str]) -> str:
        return "\n".join(f"{letter}{i + 1}: {s[:_MAX_SENT_CHARS]}" for i, s in enumerate(items)) or "(none)"

    return (
        f"You are an equity analyst reviewing {symbol}'s latest earnings call. "
        f"The lexicon read is '{read.label}' (quarter-over-quarter tone change "
        f"{read.tone_delta}). From the candidate sentences below — and ONLY these, "
        "referred to by their IDs — pick the ones that best mark a POSITIVE or "
        "NEGATIVE business inflection versus the prior quarter.\n\n"
        f"POSITIVE candidates:\n{_lines('P', pos)}\n\n"
        f"NEGATIVE candidates:\n{_lines('N', neg)}\n\n"
        "Respond EXACTLY in this format, nothing else:\n"
        "TOP_POSITIVE: <comma-separated IDs like P1, P3 — or NONE>\n"
        "TOP_NEGATIVE: <comma-separated IDs like N2 — or NONE>\n"
        "SUMMARY: <one sentence on what changed versus last quarter>"
    )


def parse_selection(
    text: str, pos: list[str], neg: list[str]
) -> tuple[list[str], list[str], str]:
    """Map the model's P#/N# selections back to verbatim sentences + one-line summary."""

    def _sel(key: str, letter: str, pool: list[str]) -> list[str]:
        m = re.search(rf"{key}\s*:(.*)", text, re.IGNORECASE)
        seg = m.group(1) if m else ""
        idxs = [int(x) for x in re.findall(rf"{letter}\s*(\d+)", seg, re.IGNORECASE)]
        seen: set[int] = set()
        out: list[str] = []
        for i in idxs:
            if 1 <= i <= len(pool) and i not in seen:
                seen.add(i)
                out.append(pool[i - 1])
        return out

    sm = re.search(r"SUMMARY\s*:(.*)", text, re.IGNORECASE | re.DOTALL)
    summary = sm.group(1).strip().splitlines()[0].strip() if sm and sm.group(1).strip() else ""
    return _sel("TOP_POSITIVE", "P", pos), _sel("TOP_NEGATIVE", "N", neg), summary


def extract_inflection(
    symbol: str,
    this_text: str,
    prior_text: str | None = None,
    *,
    llm: Completer,
    model: str | None = None,
    k: int = 6,
    max_tokens: int = 400,
) -> InflectionDetail:
    """Stage-1 read + LLM-selected inflection quotes; degrades to lexicon on LLM failure."""
    read = read_inflection(symbol, this_text, prior_text)
    pos, neg = candidate_sentences(this_text, k)
    prompt = build_prompt(symbol, read, pos, neg)
    try:
        resp = llm.complete(prompt, model=model, max_tokens=max_tokens)
        pq, nq, summary = parse_selection(resp, pos, neg)
        used = True
    except Exception:  # noqa: BLE001 — Ollama down / model missing → degrade, don't fail
        pq, nq, summary, used = pos[:3], neg[:3], "", False
    return InflectionDetail(
        read=read,
        positive_quotes=pq,
        negative_quotes=nq,
        summary=summary,
        used_llm=used,
        model=model,
    )
