"""The Gmail lane's linked-PDF matcher: public wp-content only, never mp-files."""

from __future__ import annotations

from trading_intel.letters.gmail_source import _LINK_PDF


def test_matches_public_wp_content_pdf_only() -> None:
    body = (
        "First read: https://www.jaguaranalytics.com/wp-content/uploads/2026/07/First-Read-July-27th-2026.pdf\n"
        "Note: https://www.jaguaranalytics.com/wp-content/uploads/2026/07/BSX-Note.pdf\n"
        "Gated flow: https://www.jaguaranalytics.com/mp-files/flow-73.xlsx\n"
        "Gated HA: https://www.jaguaranalytics.com/mp-files/ha-19.pdf"
    )
    found = _LINK_PDF.findall(body)
    assert any("First-Read-July-27th-2026.pdf" in u for u in found)
    assert any("BSX-Note.pdf" in u for u in found)
    # the paywalled mp-files path must never be matched (even the .pdf one)
    assert not any("mp-files" in u for u in found)
    assert len(found) == 2
