"""Tests for the deterministic RAG text chunker (pure, no I/O)."""

from __future__ import annotations

from trading_intel.memory.chunking import chunk_text


def test_empty_returns_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \t ") == []


def test_short_text_is_single_chunk():
    chunks = chunk_text("A short methodology note about dealer gamma.")
    assert len(chunks) == 1
    assert "dealer gamma" in chunks[0]


def test_packs_paragraphs_up_to_target():
    paras = "\n\n".join(f"Paragraph {i} about vanna and charm flow." for i in range(20))
    chunks = chunk_text(paras, target_chars=120, overlap=20)
    assert len(chunks) > 1
    # Each chunk should be within target plus a bounded overlap allowance.
    assert all(len(c) <= 120 + 60 for c in chunks)
    assert all(c.strip() for c in chunks)


def test_hard_splits_an_over_long_paragraph():
    giant = "vol " * 1000  # one ~4000-char paragraph, no blank lines
    chunks = chunk_text(giant, target_chars=500, overlap=50)
    assert len(chunks) >= 7
    # Overlap carry-over may push a chunk past target, but only by ~overlap.
    assert all(len(c) <= 500 + 50 + 2 for c in chunks)


def test_overlap_carries_context_between_chunks():
    # Distinct sentinel paragraphs so we can see the tail carry over.
    text = "ALPHA token paragraph one.\n\n" + ("BETA " * 60) + "\n\nGAMMA final paragraph."
    chunks = chunk_text(text, target_chars=120, overlap=40)
    assert len(chunks) >= 2
    # Consecutive chunks should share some trailing/leading text (overlap).
    shared = any(
        chunks[i][-20:].strip() and chunks[i][-20:].strip()[:10] in chunks[i + 1]
        for i in range(len(chunks) - 1)
    )
    assert shared


def test_runt_trailing_chunk_is_folded_back():
    text = ("word " * 200).strip() + "\n\nx"  # tiny trailing paragraph
    chunks = chunk_text(text, target_chars=300, overlap=30, min_chars=50)
    assert all(len(c) >= 50 or len(chunks) == 1 for c in chunks)
    # The stray "x" must not be a standalone chunk.
    assert not any(c.strip() == "x" for c in chunks)


def test_deterministic():
    text = "\n\n".join(f"Note {i}: gamma exposure regime." for i in range(15))
    assert chunk_text(text, target_chars=100) == chunk_text(text, target_chars=100)
