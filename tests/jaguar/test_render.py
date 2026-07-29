"""Pure renderer — dict-in / HTML-out, no I/O."""

from __future__ import annotations

from trading_intel.jaguar.render import build_html

SAMPLE = {
    "as_of": "Tue Jul 28 2026",
    "banner": "Real content (JaguarLive Jul 27 + First Read Jul 28).",
    "sub": "His trades → our tape → one defined-risk structure.",
    "trades": [
        {
            "ticker": "BSX",
            "name": "Boston Scientific",
            "tag": "earnings Wed 7/29 AM",
            "tag_kind": "er",
            "flow": "12,000 Dec-50 calls @ $4.48 · ~$5.4M",
            "him": "Follow the money; right company, maybe early on the calendar.",
            "ours": "Dec-50 OI build + net Δ$; Wed EM straddle.",
            "structure": {
                "label": "BSX Dec 50/60 call spread",
                "max_risk": 270.0,
                "target_pct": 2.70,
                "breakeven": 52.70,
                "note": "Rides the December window the buyer picked.",
            },
        }
    ],
    "smaller": "DBX 14,000 Aug-wk 34/36 call spread (~$210K)",
    "thinking": {
        "big_picture": "Semis the swing factor on China DUV.",
        "tactical": "GOOGL defense after the selloff.",
        "moat": ["6% of SpaceX", "top hyperscaler cloud growth"],
        "extra": "Copper miners diverging from the copper bounce.",
    },
    "breadth": {
        "index": [("DOW", "52,761 +0.72%", "g"), ("S&P", "7,446 −0.02%", "r")],
        "rows": [("% S&P above 50-day MA", "44% ▼", "58·55·52·49·44")],
        "read": "Index flat but participation draining — semis-led narrowing.",
        "foot": "S&P 500-wide via FMP constituents (rule 1).",
    },
    "changed": [("MPT", "TTWO common added Jul 28"), ("Weekend Research", "EBAY reports Aug 5")],
    "macro_facts": "Mega-cap week + Wed FOMC. NVDA circular financing.",
    "macro_read": "Every headline pokes the AI-capex trade.",
    "foot": "NAS-native; descriptive only (rule 4).",
}


def test_build_html_sections_and_escaping():
    html = build_html(SAMPLE)
    assert html.startswith("<!doctype html>")
    assert "BSX · Boston Scientific" in html
    assert "BSX Dec 50/60 call spread" in html
    assert "MAX RISK ≈ $270" in html
    assert "TARGET ~+270% on risk" in html
    assert "B/E ~$52.70" in html
    assert "% S&amp;P above 50-day MA" in html  # HTML-escaped
    assert "AI-capex trade" in html and "TTWO common added" in html
    assert "not financial advice" in html  # the caveat is always present


def test_missing_structure_and_fields_ok():
    html = build_html({"as_of": "x", "trades": [{"ticker": "T", "name": "n", "him": "h"}]})
    assert "T · n" in html
    assert "MAX RISK" not in html  # no structure → no risk row
