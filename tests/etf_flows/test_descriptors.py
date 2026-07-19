"""Tests for the LETF issuance / rebalance descriptors — pure, no Postgres."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.config import Settings
from trading_intel.etf_flows import (
    REGISTRY,
    SharesRow,
    bucket_totals,
    compute_symbol_flows,
    latest_point,
    meta_for,
)
from trading_intel.etf_flows.registry import leverage_for

_D1 = date(2026, 7, 14)
_D2 = date(2026, 7, 15)


def test_first_row_has_no_deltas_but_carries_aum():
    rows = [SharesRow(_D1, 100_000_000, nav=10.0)]
    (p,) = compute_symbol_flows("SOXL", rows, leverage=3.0)
    assert p.d_shares is None
    assert p.net_issuance_usd is None
    assert p.rebalance_notional is None
    assert p.aum == pytest.approx(1_000_000_000.0)  # shares x nav even on day 1


def test_bull_3x_issuance_and_rebalance_math():
    rows = [
        SharesRow(_D1, 100_000_000, nav=10.0),
        SharesRow(_D2, 101_000_000, nav=10.5),
    ]
    pts = compute_symbol_flows("SOXL", rows, leverage=3.0)
    p = pts[-1]
    assert p.d_shares == 1_000_000
    assert p.net_issuance_usd == pytest.approx(10_500_000.0)  # dshares x nav_t
    assert p.aum == pytest.approx(1_060_500_000.0)
    assert p.letf_return == pytest.approx(0.05)
    assert p.underlying_return == pytest.approx(0.05 / 3.0)
    # k(k-1)*AUM*und_ret = 6*AUM*(0.05/3) = 0.1*AUM  -> buy into strength (>0)
    assert p.rebalance_notional == pytest.approx(0.1 * 1_060_500_000.0)
    assert p.rebalance_notional > 0


def test_inverse_3x_rebalance_is_signed_and_procyclical():
    rows = [
        SharesRow(_D1, 50_000_000, nav=20.0),
        SharesRow(_D2, 50_000_000, nav=21.0),  # inverse fund up ⇒ underlying down
    ]
    p = compute_symbol_flows("SOXS", rows, leverage=-3.0)[-1]
    assert p.underlying_return == pytest.approx(0.05 / -3.0)  # underlying fell
    # k(k-1)=12; rebalance = 12*AUM*(-0.05/3) = -0.2*AUM  -> sell (underlying down)
    assert p.rebalance_notional == pytest.approx(-0.2 * (50_000_000 * 21.0))
    assert p.rebalance_notional < 0


def test_unknown_leverage_still_gives_issuance_but_no_rebalance():
    rows = [SharesRow(_D1, 10_000_000, nav=5.0), SharesRow(_D2, 10_200_000, nav=5.0)]
    p = compute_symbol_flows("XXXX", rows, leverage=None)[-1]
    assert p.net_issuance_usd == pytest.approx(1_000_000.0)  # 200k x 5
    assert p.underlying_return is None
    assert p.rebalance_notional is None


def test_missing_nav_blanks_dollar_terms_but_keeps_dshares():
    rows = [SharesRow(_D1, 10_000_000, nav=None), SharesRow(_D2, 10_500_000, nav=None)]
    p = compute_symbol_flows("SOXL", rows, leverage=3.0)[-1]
    assert p.d_shares == 500_000
    assert p.aum is None
    assert p.net_issuance_usd is None
    assert p.letf_return is None


def test_rows_are_sorted_by_ts():
    rows = [SharesRow(_D2, 101_000_000, nav=10.5), SharesRow(_D1, 100_000_000, nav=10.0)]
    pts = compute_symbol_flows("SOXL", rows, leverage=3.0)
    assert [p.ts for p in pts] == [_D1, _D2]
    assert latest_point(pts).ts == _D2


def test_bucket_totals_sums_by_issuer():
    soxl = compute_symbol_flows(
        "SOXL", [SharesRow(_D1, 100, nav=10.0), SharesRow(_D2, 110, nav=10.0)], leverage=3.0
    )
    tqqq = compute_symbol_flows(
        "TQQQ", [SharesRow(_D1, 200, nav=50.0), SharesRow(_D2, 190, nav=50.0)], leverage=3.0
    )
    points = [latest_point(soxl), latest_point(tqqq)]
    totals = bucket_totals(points, lambda s: meta_for(s).issuer)
    assert totals["Direxion"]["net_issuance_usd"] == pytest.approx(100.0)  # +10 x 10
    assert totals["ProShares"]["net_issuance_usd"] == pytest.approx(-500.0)  # -10 x 50
    assert totals["Direxion"]["n"] == 1.0


def test_registry_covers_the_configured_universe():
    """Guard against drift: every default LETF_SYMBOLS entry has reference data."""
    default = str(Settings.model_fields["LETF_SYMBOLS"].default)
    configured = {s.strip().upper() for s in default.split(",") if s.strip()}
    assert configured, "expected a non-empty default LETF_SYMBOLS"
    missing = configured - set(REGISTRY)
    assert not missing, f"registry missing leverage/issuer for: {sorted(missing)}"


def test_leverage_lookup_is_case_insensitive():
    assert leverage_for("soxl") == 3.0
    assert leverage_for("SOXS") == -3.0
    assert leverage_for("NOT_A_TICKER") is None
