"""Source-side ticker gate for the watchlist ingest (pure function)."""

from __future__ import annotations

from trading_intel.memory.watchlist_ingest import _is_valid_ticker


def test_valid_tickers_pass() -> None:
    for good in ["AAPL", "F", "TSLA", "NVDA", "SG", "MELI"]:
        assert _is_valid_ticker(good), good


def test_junk_is_dropped() -> None:
    # misspellings / acronyms in the stoplist, dotted or over-long or empty tokens
    for bad in ["NVDIA", "THE", "GDP", "IPO", "IPIA.FIL", "TOOLONG", "123", "A.B", "", None]:
        assert not _is_valid_ticker(bad), bad
