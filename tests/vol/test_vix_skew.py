"""Tests for the VIX-options skew analytics (``vol.vix_skew``)."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.vol.vix_skew import (
    vix_call_oi_share,
    vix_call_premium_share,
    vix_call_skew,
    vix_call_wing_iv,
    vix_tail_hedging_score,
    vix_term_call_skew,
)


def _make_vix_chain() -> pd.DataFrame:
    """Hand-built VIX-options chain with a clear OTM-call bid.

    Two expiries; calls richer than puts at the wings; OI concentrated on the
    OTM call side — the structural VIX tail-hedge picture.
    """
    rows = [
        # 30d expiry (2026-06-25) — ATM call + put, 25d call (hot OTM wing), 25d put,
        # 10d call (far-OTM tail with the biggest OI), 10d put.
        {"expiration": "2026-06-25", "strike": 20, "opt_kind": "call",
         "delta": 0.50, "iv": 0.80, "oi": 1000, "volume": 200},
        {"expiration": "2026-06-25", "strike": 20, "opt_kind": "put",
         "delta": -0.50, "iv": 0.80, "oi": 900, "volume": 180},
        {"expiration": "2026-06-25", "strike": 30, "opt_kind": "call",
         "delta": 0.25, "iv": 1.10, "oi": 5000, "volume": 1500},
        {"expiration": "2026-06-25", "strike": 15, "opt_kind": "put",
         "delta": -0.25, "iv": 0.75, "oi": 400, "volume": 80},
        {"expiration": "2026-06-25", "strike": 45, "opt_kind": "call",
         "delta": 0.10, "iv": 1.40, "oi": 8000, "volume": 2200},
        {"expiration": "2026-06-25", "strike": 12, "opt_kind": "put",
         "delta": -0.10, "iv": 0.70, "oi": 200, "volume": 40},
        # 60d expiry (2026-07-25)
        {"expiration": "2026-07-25", "strike": 20, "opt_kind": "call",
         "delta": 0.50, "iv": 0.78, "oi": 600, "volume": 120},
        {"expiration": "2026-07-25", "strike": 20, "opt_kind": "put",
         "delta": -0.50, "iv": 0.78, "oi": 550, "volume": 110},
        {"expiration": "2026-07-25", "strike": 30, "opt_kind": "call",
         "delta": 0.25, "iv": 1.00, "oi": 2500, "volume": 700},
        {"expiration": "2026-07-25", "strike": 15, "opt_kind": "put",
         "delta": -0.25, "iv": 0.74, "oi": 200, "volume": 40},
    ]
    df = pd.DataFrame(rows)
    df["expiration"] = pd.to_datetime(df["expiration"])
    return df


# ── Wing IV ────────────────────────────────────────────────────────────


def test_vix_call_wing_iv_nearest_expiry_default():
    chain = _make_vix_chain()
    assert vix_call_wing_iv(chain, abs_delta=0.25) == pytest.approx(1.10)


def test_vix_call_wing_iv_explicit_expiry():
    chain = _make_vix_chain()
    later = pd.Timestamp("2026-07-25")
    assert vix_call_wing_iv(chain, abs_delta=0.25, expiry=later) == pytest.approx(1.00)


def test_vix_call_wing_iv_none_on_empty_chain():
    assert vix_call_wing_iv(pd.DataFrame(), abs_delta=0.25) is None


# ── Call skew ──────────────────────────────────────────────────────────


def test_vix_call_skew_is_wing_minus_atm():
    chain = _make_vix_chain()
    # 30d: 25Δ call IV 1.10, ATM 0.80 -> +0.30 (positive = structural call bid)
    assert vix_call_skew(chain, abs_delta=0.25) == pytest.approx(0.30)


def test_vix_call_skew_none_when_no_calls():
    puts_only = _make_vix_chain()
    puts_only = puts_only.loc[puts_only["opt_kind"] == "put"]
    assert vix_call_skew(puts_only, abs_delta=0.25) is None


# ── Term call skew ─────────────────────────────────────────────────────


def test_vix_term_call_skew_ordered_ascending():
    chain = _make_vix_chain()
    term = vix_term_call_skew(chain, abs_delta=0.25, n_expiries=3)
    assert [t[0] for t in term] == [pd.Timestamp("2026-06-25"), pd.Timestamp("2026-07-25")]
    # Front-month skew (0.30) > back-month skew (0.22) — front-loaded tail bid.
    assert term[0][1] > term[1][1]


def test_vix_term_call_skew_empty_when_chain_empty():
    assert vix_term_call_skew(pd.DataFrame(), abs_delta=0.25) == []


# ── OI share ───────────────────────────────────────────────────────────


def test_vix_call_oi_share_positive_for_tail_skewed_book():
    chain = _make_vix_chain()
    share = vix_call_oi_share(chain, otm_delta_cutoff=0.30)
    # Numerator: 30d OTM calls 5000+8000 + 60d 2500 = 15500
    # Denominator: all OI = 1000+900+5000+400+8000+200+600+550+2500+200 = 19350
    assert share == pytest.approx(15500 / 19350)


def test_vix_call_oi_share_none_when_oi_missing():
    chain = pd.DataFrame({"opt_kind": [], "delta": [], "oi": []})
    assert vix_call_oi_share(chain) is None


# ── Premium share ──────────────────────────────────────────────────────


def test_vix_call_premium_share_in_zero_one():
    chain = _make_vix_chain()
    share = vix_call_premium_share(chain, otm_delta_cutoff=0.30)
    assert share is not None and 0.0 < share < 1.0
    # Wing notional should dominate ATM (high volume * high IV at OTM calls).
    assert share > 0.5


# ── Composite z-sum ────────────────────────────────────────────────────


def test_vix_tail_hedging_score_sums_inputs():
    assert vix_tail_hedging_score(
        call_skew_z=1.5, oi_share_z=0.5, vvix_vix_z=2.0
    ) == pytest.approx(4.0)


def test_vix_tail_hedging_score_skips_nones():
    assert vix_tail_hedging_score(
        call_skew_z=1.5, oi_share_z=None, vvix_vix_z=2.0
    ) == pytest.approx(3.5)


def test_vix_tail_hedging_score_none_when_all_missing():
    assert vix_tail_hedging_score(
        call_skew_z=None, oi_share_z=None, vvix_vix_z=None
    ) is None


def test_vix_tail_hedging_score_skips_nan():
    assert vix_tail_hedging_score(
        call_skew_z=float("nan"), oi_share_z=2.0, vvix_vix_z=None
    ) == pytest.approx(2.0)
