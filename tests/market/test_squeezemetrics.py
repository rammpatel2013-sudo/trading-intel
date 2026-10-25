"""Tests for the SqueezeMetrics CSV parser + percentile helpers (pure)."""

from __future__ import annotations

from datetime import date

from trading_intel.market.squeezemetrics import gex_regime_label, parse_dix_csv, percentile_rank

_CSV = """date,price,dix,gex
2026-10-21,7700.1,0.44,2500000000
2026-10-22,7663.43,0.46265353846999696,3168053270.8299975

bad-line
2026-10-23,7623.68,0.4659628509981996,4001421424.610004
2026-10-23,7623.68,0.47,4100000000
2026-10-24,x,0.5,1
"""


def test_parse_dix_csv_sorted_deduped_tolerant() -> None:
    rows = parse_dix_csv(_CSV)
    assert [r["date"] for r in rows] == [
        date(2026, 10, 21),
        date(2026, 10, 22),
        date(2026, 10, 23),
        date(2026, 10, 24),
    ]
    # duplicate date: last one wins
    assert rows[2]["gex"] == 4100000000.0
    # bad numeric cell -> None, row kept
    assert rows[3]["spx_price"] is None and rows[3]["dix"] == 0.5
    assert rows[0]["dix"] == 0.44


def test_percentile_rank_ties_and_edges() -> None:
    hist = [1.0, 2.0, 3.0, 4.0]
    assert percentile_rank(hist, 5.0) == 100.0
    assert percentile_rank(hist, 0.0) == 0.0
    assert percentile_rank(hist, 2.5) == 50.0
    assert percentile_rank([2.0, 2.0, 2.0], 2.0) == 50.0  # all ties -> 50
    assert percentile_rank([None, None], 1.0) is None
    assert percentile_rank(hist, None) is None


def test_gex_regime_label() -> None:
    assert gex_regime_label(10.0) == "low"
    assert gex_regime_label(30.0) == "low"
    assert gex_regime_label(50.0) == "mid"
    assert gex_regime_label(70.0) == "high"
    assert gex_regime_label(None) == "n/a"
