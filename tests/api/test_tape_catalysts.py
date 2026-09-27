"""Tape & catalysts — news tagging and revenue trend."""

from __future__ import annotations

from trading_intel.api.tape_catalysts import render_section, revenue_trend, tag_news


def test_noise_headlines_dropped_and_regulatory_tagged() -> None:
    items = [
        {"title": "State Street Corp Grows Holdings in ImmunityBio, Inc. $IBRX"},
        {"title": "XYZ Receives $14.20 Consensus Target Price from Brokerages"},
        {"title": "US FDA approves Vera's kidney disease drug", "publishedDate": "2026-07-07 10:00"},
        {"title": "Vera Stock Slides Despite Positive Phase 3 Results"},
        {"title": "Company prices $200M public offering"},
    ]
    out = tag_news(items)
    assert [o["tags"] for o in out] == [["regulatory"], ["clinical"], ["capital"]]
    assert out[0]["date"] == "2026-07-07"


def test_body_only_adds_hard_regulatory_hits() -> None:
    out = tag_news([{"title": "Why the stock is moving", "text": "after the FDA set a PDUFA date"}])
    assert out[0]["tags"] == ["regulatory"]
    out = tag_news([{"title": "Why the stock is moving", "text": "quarterly results were strong"}])
    assert out[0]["tags"] == []


def test_revenue_trend_qoq_yoy() -> None:
    stmts = [{"date": f"2025-0{i}-30", "revenue": 100 + 10 * i} for i in range(1, 7)]
    rt = revenue_trend(stmts)
    assert rt[0]["qoq"] is None and round(rt[1]["qoq"], 4) == round(120 / 110 - 1, 4)
    assert round(rt[4]["yoy"], 4) == round(150 / 110 - 1, 4)


def test_render_handles_empty() -> None:
    html = render_section({"symbol": "X", "tape": None, "tape_read": [], "revenue": [],
                           "catalysts": [], "next_earnings": None})
    assert "Tape &amp; catalysts" in html and "no statements" in html
