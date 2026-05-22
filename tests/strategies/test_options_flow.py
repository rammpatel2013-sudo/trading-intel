"""Tests for aggregate options-flow summary — pure, no network."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.strategies.options_flow import aggregate_flow


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "opt_kind": "put",
                "premium": 114e6,
                "strike": 7000,
                "expiration": "2026-06-18",
                "iv": 0.22,
                "signed": -50e6,
            },
            {
                "opt_kind": "put",
                "premium": 114e6,
                "strike": 6500,
                "expiration": "2026-07-17",
                "iv": 0.25,
                "signed": -30e6,
            },
            {
                "opt_kind": "call",
                "premium": 136e6,
                "strike": 7455,
                "expiration": "2026-06-18",
                "iv": 0.15,
                "signed": 20e6,
            },
        ]
    )


def test_aggregate_flow_notional_and_tilt():
    f = aggregate_flow(_chain(), top_n=2)
    assert f.put_notional == pytest.approx(228e6)
    assert f.call_notional == pytest.approx(136e6)
    assert f.put_call_ratio == pytest.approx(228 / 136, rel=1e-3)
    assert f.tilt == "defensive (put-heavy)"
    assert f.net_premium == pytest.approx(-60e6)
    assert f.n_prints == 3
    assert len(f.top_prints) == 2
    assert f.top_prints[0]["premium"] >= f.top_prints[1]["premium"]


def test_aggregate_flow_balanced_and_offensive():
    bal = aggregate_flow(
        pd.DataFrame([{"opt_kind": "call", "premium": 100}, {"opt_kind": "put", "premium": 100}])
    )
    assert bal.tilt == "balanced"
    off = aggregate_flow(
        pd.DataFrame([{"opt_kind": "call", "premium": 200}, {"opt_kind": "put", "premium": 100}])
    )
    assert off.tilt == "offensive (call-heavy)"


def test_aggregate_flow_errors():
    with pytest.raises(ComputationError):
        aggregate_flow(pd.DataFrame())
    with pytest.raises(ComputationError):
        aggregate_flow(pd.DataFrame([{"opt_kind": "call"}]))  # missing premium


# ── flowsum_by_expiry ───────────────────────────────────────────────────────

from trading_intel.strategies.options_flow import flowsum_by_expiry  # noqa: E402


def _flowsum_chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "expiration": "2026-05-22",
                "opt_kind": "call",
                "volm_buy": 100,
                "volm_sell": 60,
                "oi": 1000,
                "gxoi": 5.0,
                "vommaxoi": 200.0,
            },
            {
                "expiration": "2026-05-22",
                "opt_kind": "put",
                "volm_buy": 40,
                "volm_sell": 90,
                "oi": 1500,
                "gxoi": -3.0,
                "vommaxoi": -50.0,
            },
            {
                "expiration": "2026-06-18",
                "opt_kind": "call",
                "volm_buy": 10,
                "volm_sell": 5,
                "oi": 200,
                "gxoi": 1.0,
                "vommaxoi": 30.0,
            },
        ]
    )


def test_flowsum_by_expiry():
    out = flowsum_by_expiry(_flowsum_chain())
    # 2 expiries x (calls, puts, total) = 6 rows
    assert len(out) == 6
    front = out[(out["expiry"] == "2026-05-22")]
    calls = front[front["side"] == "calls"].iloc[0]
    puts = front[front["side"] == "puts"].iloc[0]
    total = front[front["side"] == "total"].iloc[0]
    assert calls["volm_bs"] == 40  # 100 - 60
    assert puts["volm_bs"] == -50  # 40 - 90
    assert total["oi"] == 2500  # 1000 + 1500
    assert total["vommaxoi"] == 150.0  # 200 + (-50)


def test_flowsum_by_expiry_errors():
    with pytest.raises(ComputationError):
        flowsum_by_expiry(pd.DataFrame())


# ── detect_structures (per-trade multi-leg packages) ─────────────────────────

from trading_intel.strategies.options_flow import (  # noqa: E402
    detect_structures,
    format_structures_markdown,
)


def _tas() -> pd.DataFrame:
    t1 = pd.Timestamp("2026-05-22 00:54:57.501")
    t2 = pd.Timestamp("2026-05-22 00:48:17.809")
    t3 = pd.Timestamp("2026-05-22 00:24:08.959")
    return pd.DataFrame(
        [
            # t1: SPXW call spread, both sold
            {"time": t1, "root": "SPXW", "expiration": "2026-05-22", "strike": 7400.0,
             "opt_kind": "call", "size": 50, "premium": 371500.0, "aggressor_side": "sell"},
            {"time": t1, "root": "SPXW", "expiration": "2026-05-22", "strike": 7430.0,
             "opt_kind": "call", "size": 50, "premium": 247000.0, "aggressor_side": "sell"},
            # t2: SPX put spread, sell near / buy far
            {"time": t2, "root": "SPX", "expiration": "2026-06-18", "strike": 7475.0,
             "opt_kind": "put", "size": 20, "premium": 216880.0, "aggressor_side": "sell"},
            {"time": t2, "root": "SPX", "expiration": "2026-06-18", "strike": 7650.0,
             "opt_kind": "put", "size": 10, "premium": 205780.0, "aggressor_side": "buy"},
            # t3: lone SPX put -> single
            {"time": t3, "root": "SPX", "expiration": "2026-11-20", "strike": 7150.0,
             "opt_kind": "put", "size": 25, "premium": 528825.0, "aggressor_side": "buy"},
        ]
    )


def test_detect_structures_classifies_and_signs():
    structs = detect_structures(_tas())
    # 3 tickets: SPXW call spread, SPX put spread, lone SPX put
    assert len(structs) == 3
    by_kind = {s.kind: s for s in structs}
    assert "call spread" in by_kind
    assert "put spread" in by_kind
    assert "single" in by_kind

    cs = by_kind["call spread"]
    assert cs.n_legs == 2
    assert cs.total_premium == pytest.approx(618500.0)
    assert cs.net_premium == pytest.approx(-618500.0)  # both sells

    ps = by_kind["put spread"]
    assert ps.net_premium == pytest.approx(205780.0 - 216880.0)  # buy far - sell near

    # sorted by total premium descending
    assert structs[0].total_premium >= structs[-1].total_premium


def test_detect_structures_min_premium_filter():
    structs = detect_structures(_tas(), min_premium=600000.0)
    # only the SPXW call spread (618.5k) and the lone SPX put (528.8k<600k -> out)
    assert [s.kind for s in structs] == ["call spread"]


def test_detect_structures_empty_and_missing():
    assert detect_structures(pd.DataFrame()) == []
    with pytest.raises(ComputationError):
        detect_structures(pd.DataFrame([{"time": 1, "root": "SPX"}]))


def test_format_structures_markdown():
    md = format_structures_markdown(detect_structures(_tas()))
    assert md.startswith("## Notable packages")
    assert "call spread" in md and "put spread" in md
    # singles are excluded from the package list
    assert "single" not in md


def test_format_structures_markdown_none():
    md = format_structures_markdown([])
    assert "No multi-leg packages" in md


def test_detect_structures_collapses_repeat_fills():
    """Repeat fills of the same contract collapse to one leg (true structure)."""
    t = pd.Timestamp("2026-05-22 00:54:57.501")
    legs = pd.DataFrame(
        [
            {"time": t, "root": "SPXW", "expiration": "2026-05-22", "strike": 7400.0,
             "opt_kind": "call", "size": 50, "premium": 100000.0, "aggressor_side": "sell"},
            {"time": t, "root": "SPXW", "expiration": "2026-05-22", "strike": 7430.0,
             "opt_kind": "call", "size": 50, "premium": 80000.0, "aggressor_side": "sell"},
            # repeat fills of the same two contracts
            {"time": t, "root": "SPXW", "expiration": "2026-05-22", "strike": 7400.0,
             "opt_kind": "call", "size": 50, "premium": 100000.0, "aggressor_side": "sell"},
            {"time": t, "root": "SPXW", "expiration": "2026-05-22", "strike": 7430.0,
             "opt_kind": "call", "size": 50, "premium": 80000.0, "aggressor_side": "sell"},
        ]
    )
    structs = detect_structures(legs)
    assert len(structs) == 1
    s = structs[0]
    assert s.n_legs == 2  # collapsed from 4 raw prints
    assert s.kind == "call spread"
    assert s.total_premium == pytest.approx(360000.0)  # all raw premium retained
    assert s.expirations == ["2026-05-22"]  # date-formatted, no 00:00:00
    # collapsed leg sizes summed
    by_strike = {leg["strike"]: leg for leg in s.legs}
    assert by_strike[7400.0]["size"] == pytest.approx(100.0)
    assert by_strike[7400.0]["premium"] == pytest.approx(200000.0)


# ── format_flowsum_markdown ──────────────────────────────────────────────────

from trading_intel.strategies.options_flow import format_flowsum_markdown  # noqa: E402


def test_format_flowsum_markdown():
    md = format_flowsum_markdown(flowsum_by_expiry(_flowsum_chain()))
    assert md.startswith("## Greek-OI by expiry (flowsum)")
    assert "2026-05-22" in md
    assert "GxOI" in md
    # one bullet per expiry total (2 expiries)
    assert md.count("\n- ") == 2


def test_format_flowsum_markdown_empty():
    assert "No flow-summary data available." in format_flowsum_markdown(pd.DataFrame())


def test_detect_structures_spread_leg_excludes_outrights():
    """spread_leg=False outrights must not be swept into a same-ms package."""
    t = pd.Timestamp("2026-05-22 03:31:18.331")
    df = pd.DataFrame(
        [
            # genuine 2-leg put spread, both flagged as spread legs
            {"time": t, "root": "SPX", "expiration": "2026-06-18", "strike": 7450.0,
             "opt_kind": "put", "size": 250, "premium": 2457250.0,
             "aggressor_side": "sell", "spread_leg": True},
            {"time": t, "root": "SPX", "expiration": "2026-06-18", "strike": 7150.0,
             "opt_kind": "put", "size": 425, "premium": 1431825.0,
             "aggressor_side": "buy", "spread_leg": True},
            # coincidental outright at the SAME ms, different expiry — must stay separate
            {"time": t, "root": "SPX", "expiration": "2026-11-20", "strike": 7000.0,
             "opt_kind": "put", "size": 10, "premium": 300000.0,
             "aggressor_side": "buy", "spread_leg": False},
        ]
    )
    structs = detect_structures(df)
    by_kind = {s.kind: s for s in structs}
    assert "put spread" in by_kind
    ps = by_kind["put spread"]
    assert ps.n_legs == 2
    # the Nov outright did NOT leak into the package (no false calendar/diagonal)
    assert ps.expirations == ["2026-06-18"]
    singles = [s for s in structs if s.kind == "single"]
    assert len(singles) == 1
    assert singles[0].legs[0]["strike"] == 7000.0
