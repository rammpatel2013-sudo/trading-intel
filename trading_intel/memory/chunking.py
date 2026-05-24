"""Deterministic text chunking for the RAG substrate.

Splits a document's *full* extracted text into overlapping chunks suitable for
embedding into the ``chunks`` pgvector store. Pure and dependency-free — the
embedding + persistence happen in ``memory/pdf_pipeline`` / ``memory/retrieval``;
this module only decides where the cut lines go, so it is fully unit-testable.

Strategy: split on blank lines into paragraphs, hard-split any paragraph longer
than ``target_chars`` into overlapping windows, then greedily pack paragraphs up
to ``target_chars``. A small character overlap is carried between adjacent chunks
so a concept that straddles a cut line is still retrievable from both sides.
"""

from __future__ import annotations

import re

#: Default chunk sizing. nomic-embed-text handles ~8k tokens; ~1200 chars (~300
#: tokens) keeps each chunk topically tight for cosine retrieval.
DEFAULT_TARGET_CHARS = 1200
DEFAULT_OVERLAP = 150
DEFAULT_MIN_CHARS = 80

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+")
_WS = re.compile(r"[ \t]+")


def _normalize(text: str) -> str:
    """Collapse runs of spaces/tabs and trim, keeping paragraph breaks."""
    lines = [_WS.sub(" ", ln).strip() for ln in (text or "").splitlines()]
    return "\n".join(lines)


def _paragraphs(text: str) -> list[str]:
    parts = _PARAGRAPH_SPLIT.split(text)
    return [p.strip() for p in parts if p.strip()]


def _hard_split(para: str, target_chars: int, overlap: int) -> list[str]:
    """Window an over-long paragraph into ``target_chars`` slices with overlap."""
    stride = max(1, target_chars - overlap)
    out: list[str] = []
    start = 0
    n = len(para)
    while start < n:
        out.append(para[start : start + target_chars].strip())
        if start + target_chars >= n:
            break
        start += stride
    return [s for s in out if s]


def _overlap_tail(text: str, overlap: int) -> str:
    """Return the trailing ``overlap`` chars of ``text``, aligned to a word."""
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail


def chunk_text(
    text: str,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    min_chars: int = DEFAULT_MIN_CHARS,
) -> list[str]:
    """Split ``text`` into overlapping chunks.

    Returns ``[]`` for empty/whitespace input. Every returned chunk is
    non-empty; a too-small trailing chunk is merged back into its predecessor so
    we never emit a stub. Overlap is best-effort context carry-over, so chunks
    may slightly exceed ``target_chars``.
    """
    normalized = _normalize(text)
    if not normalized.strip():
        return []

    units: list[str] = []
    for para in _paragraphs(normalized):
        if len(para) <= target_chars:
            units.append(para)
        else:
            units.extend(_hard_split(para, target_chars, overlap))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 2 + len(unit) > target_chars:
            chunks.append(current)
            tail = _overlap_tail(current, overlap)
            current = f"{tail}\n\n{unit}" if tail else unit
        else:
            current = unit if not current else f"{current}\n\n{unit}"
    if current:
        chunks.append(current)

    # Fold a runt trailing chunk into the previous one.
    if len(chunks) >= 2 and len(chunks[-1]) < min_chars:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()

    return [c for c in chunks if c.strip()]
