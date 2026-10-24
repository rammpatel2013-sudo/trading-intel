"""Unit tests for the cyclical-vs-defensive gamma split (sector_scan.gamma_split)."""

from __future__ import annotations

from trading_intel.market.sector_scan import build_sector_scan, classify_sector, gamma_split


def _row(sym, gex, spot=100.0, flip=None):
    return dict(symbol=sym, spot=spot, gex_total=gex, gex_flip=flip)


def test_fragile_index_when_cyc_short_def_long():
    rows = [_row("XLK", -55e6, 187, 223), _row("XLF", -100e6, 55, 56.5), _row("XLU", 96e6, 41.5, 38), _row("XLP", -31e6, 87, 93)]
    gs = gamma_split([classify_sector(r) for r in rows])
    assert gs["label"] == "fragile-index"
    assert gs["cyclical"]["sign"] == "short" and gs["cyclical"]["n_below_flip"] == 2
    assert gs["defensive"]["sign"] == "long"  # 96 − 31 > 0
    assert gs["defensive"]["short_gamma"] == ["XLP"]


def test_other_quadrants_and_pending():
    assert gamma_split([classify_sector(_row("XLK", 1e6)), classify_sector(_row("XLU", 1e6))])["label"] == "broad-pin"
    assert gamma_split([classify_sector(_row("XLK", -1e6)), classify_sector(_row("XLU", -1e6))])["label"] == "broad-short"
    assert gamma_split([classify_sector(_row("XLK", 1e6)), classify_sector(_row("XLU", -1e6))])["label"] == "cushioned-index"
    assert gamma_split([])["label"] == "pending"
    assert gamma_split([classify_sector(_row("XLK", -1e6))])["label"] == "mixed"


def test_history_sums_only_fully_priced_days():
    rows = [_row("XLK", -5.0), _row("XLF", -3.0)]
    hist = {"XLK": [("d1", -4.0), ("d2", -5.0)], "XLF": [("d2", -3.0)]}
    gs = gamma_split([classify_sector(r) for r in rows], gex_history=hist)
    assert gs["cyclical"]["history"] == [-8.0]  # d1 lacks XLF → dropped


def test_wired_into_scan():
    v = build_sector_scan([_row("XLK", -1.0), _row("XLU", 1.0)], corr=None, internals=None)
    assert v["gamma_split"]["label"] == "fragile-index"
