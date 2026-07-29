"""Deterministic Jaguar parser — grounded on real JaguarLive shapes, no network/LLM."""

from __future__ import annotations

from trading_intel.jaguar import parse

BODY = """Good Morning Jags!
16 hours ago

Weekend Research - July 27 (https://www.jaguaranalytics.com/wp-content/uploads/2026/07/Weekend-Research-July-26th-2026.pdf)

eBay (EBAY) - A proprietary index with a 94% correlation points to GMV upside when this company reports on August 5th.

Ligand Pharmaceuticals (LGND) - Reiterating the bull case for this royalty company.
16 hours ago

First Read - July 27

Highlights from today's First Read:
* Iran signals it will stop its attacks on Gulf neighbors.
* Notable Callout: Goldman Sachs says US equities have typically traded sideways ahead of midterms.
14 hours ago

Herc Holdings (HRI) - Earnings preview: Herc reports Tuesday, July 28th before the open. $5.77 million of bullish premium across two opening prints against exactly zero bearish.
* Research note, see HERE (https://www.jaguaranalytics.com/wp-content/uploads/2026/07/HRI-Note.pdf)
9 hours ago

Boston Scientific (BSX) - I will likely follow the money here. Bullish earnings preview. Someone bought 12,000 December 50-strike calls at $4.48 on the offer, roughly $5.4 million of premium on 2.1 times the daily average call volume.
8 hours ago

Golar LNG (GLNG) - Moments ago somebody bought 10,000 contracts of September 55 calls for up to $0.90 offer. Approx $850,000 bullish bet.
Img
8 hours ago"""


def test_callouts_extract_tickers_and_flags():
    cos = {c.ticker: c for c in parse.parse_callouts(BODY)}
    assert {"HRI", "BSX", "GLNG"} <= set(cos)
    # Weekend-Research names live inside their section block, not as flow callouts
    assert "EBAY" not in cos and "LGND" not in cos
    assert cos["HRI"].earnings is True
    assert cos["BSX"].earnings is True
    assert cos["GLNG"].earnings is False


def test_premium_is_trade_not_revenue():
    hri = {c.ticker: c for c in parse.parse_callouts(BODY)}["HRI"]
    assert hri.premium == "$5.77 million"  # the bullish-premium figure, banked correctly


def test_contract_keeps_the_price_decimal():
    bsx = {c.ticker: c for c in parse.parse_callouts(BODY)}["BSX"]
    assert bsx.contracts and "12,000 December 50-strike calls at $4.48" in bsx.contracts[0]


def test_links_are_public_only():
    hri = {c.ticker: c for c in parse.parse_callouts(BODY)}["HRI"]
    assert any("HRI-Note.pdf" in u for u in hri.links)
    assert all("mp-files" not in u for u in parse.pdf_links(BODY))


def test_sections_and_highlights():
    wr = parse.find_block(BODY, "Weekend Research")
    assert wr is not None and "EBAY" in wr and "LGND" in wr
    hl = parse.first_read_highlights(BODY)
    assert any("Notable Callout" in h for h in hl)
    assert any("Iran" in h for h in hl)
