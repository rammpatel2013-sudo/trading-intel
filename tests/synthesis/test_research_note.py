"""Tests for the research-note synthesis (pure formatting + deterministic fallback)."""

from __future__ import annotations

from trading_intel.synthesis.research_note import build_research_note, format_fundamentals


def test_format_fundamentals():
    out = format_fundamentals(
        {"companyName": "Apple Inc.", "sector": "Tech", "industry": "Hardware",
         "mktCap": 3_000_000, "description": "Makes phones."},
        [{"calendarYear": "2024", "revenue": 391000, "netIncome": 93700,
          "grossProfitRatio": 0.46}],
        [{"publishedDate": "2026-05-01T10:00:00", "title": "Apple ships chips", "site": "Reuters"}],
    )
    assert "Apple Inc." in out and "FY2024" in out and "Apple ships chips" in out


def test_format_fundamentals_empty():
    assert "no fundamentals" in format_fundamentals(None, None, None)


def test_build_note_deterministic_fallback():
    # llm=None -> deterministic fallback (no Ollama needed).
    note = build_research_note(
        "AAPL",
        llm=None,
        pdf_text="The thesis: AI supercycle drives services.",
        profile={"companyName": "Apple Inc.", "sector": "Tech"},
        regime_md="Positive gamma; contango; VRP +6.",
    )
    assert "AAPL" in note
    assert "Apple Inc." in note
    assert "Positive gamma" in note
    assert "AI supercycle" in note
