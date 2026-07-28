"""Tests for the source registry (dedup)."""

from __future__ import annotations

from trading_intel.letters.sources import edgar_13f_sources, substack_sources


def test_substack_sources_unique_feeds():
    feeds = [s.ref for s in substack_sources()]
    assert len(feeds) == len(set(feeds))
    assert all(f.endswith("/feed") for f in feeds)


def test_edgar_sources_unique_ciks_and_kind():
    ciks = [s.ref for s in edgar_13f_sources()]
    assert len(ciks) == len(set(ciks))
    assert all(s.kind == "edgar_13f" for s in edgar_13f_sources())
    assert all(s.ref.isdigit() for s in edgar_13f_sources())
