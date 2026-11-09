"""Pure maths for the tenor gamma / vega view (2026-11-09)."""
# ruff: noqa: N806  (S, K = spot, strike)


from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from trading_intel.greeks import tenor_buckets as tb
from trading_intel.greeks.black_scholes import bs_call_price, bs_gamma, bs_vega
from trading_intel.greeks.exposure_profile import greek_profiles

BOOK = date(2026, 11, 6)  # Friday


def _book() -> pd.DataFrame:
    rows = []
    for exp in (date(2026, 11, 9), date(2026, 11, 13), date(2026, 11, 20), date(2026, 12, 18)):
        for k in (7200.0, 7300.0, 7400.0, 7500.0):
            rows.append({"strike": k, "cp": "C", "iv": 0.18, "oi": 1000, "expiry": exp})
            rows.append({"strike": k, "cp": "P", "iv": 0.22, "oi": 3000, "expiry": exp})
    return pd.DataFrame(rows)


@pytest.mark.parametrize("k,sig,t", [(7400.0, 0.2, 0.05), (7000.0, 0.3, 0.5), (7800.0, 0.15, 0.01)])
def test_bs_gamma_matches_finite_difference(k, sig, t):
    S, h = 7400.0, 0.5
    K, s_, tt = np.array([k]), np.array([sig]), np.array([t])
    fd = (
        bs_call_price(S + h, K, s_, tt)
        - 2 * bs_call_price(S, K, s_, tt)
        + bs_call_price(S - h, K, s_, tt)
    ) / h**2
    assert bs_gamma(S, K, s_, tt)[0] == pytest.approx(fd[0], rel=1e-4)


@pytest.mark.parametrize("k,sig,t", [(7400.0, 0.2, 0.05), (7000.0, 0.3, 0.5)])
def test_bs_vega_matches_finite_difference(k, sig, t):
    S, h = 7400.0, 1e-4
    K, tt = np.array([k]), np.array([t])
    fd = (
        bs_call_price(S, K, np.array([sig + h]), tt) - bs_call_price(S, K, np.array([sig - h]), tt)
    ) / (2 * h)
    assert bs_vega(S, K, np.array([sig]), tt)[0] == pytest.approx(fd[0], rel=1e-6)


def test_sessions_skip_weekend_and_thanksgiving():
    assert tb.sessions_to_expiry(BOOK, date(2026, 11, 9)) == 1  # Fri -> Mon
    assert tb.sessions_to_expiry(BOOK, date(2026, 11, 13)) == 5
    # 11/26 is a holiday: Fri 11/20 -> Fri 11/27 is 4 sessions
    assert tb.sessions_to_expiry(date(2026, 11, 20), date(2026, 11, 27)) == 4
    assert tb.sessions_to_expiry(BOOK, BOOK) == 0
    assert tb.sessions_to_expiry(date(2026, 11, 25), date(2026, 11, 26)) == 1  # expiry on a holiday


@pytest.mark.parametrize(
    "n,key",
    [(0, "1d"), (1, "1d"), (2, "2d_1w"), (5, "2d_1w"), (6, "1w_1m"), (21, "1w_1m"), (22, "gt_1m")],
)
def test_bucket_edges(n, key):
    assert tb.bucket_for(n) == key


def test_bucketing_of_real_calendar():
    df = tb.prepare_chain(_book(), BOOK)
    got = dict(zip(df["expiry"], df["bucket"], strict=True))
    assert got[date(2026, 11, 9)] == "1d"
    assert got[date(2026, 11, 13)] == "2d_1w"
    assert got[date(2026, 11, 20)] == "1w_1m"
    assert got[date(2026, 12, 18)] == "gt_1m"  # 28 sessions (Thanksgiving skipped)


def test_zero_cross_linear_and_nearest():
    g = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    assert tb.zero_cross(g, np.array([-2, -1, 1, 2, 3]), 2.0) == pytest.approx(1.5)
    assert tb.zero_cross(g, np.array([-1, -1, -1, -1, -1]), 2.0) is None
    two = np.array([1, -1, -1, -1, 1])  # crosses at 0.5 and 3.5
    assert tb.zero_cross(g, two, 3.2) == pytest.approx(3.5)
    assert tb.zero_cross(g, two, 0.2) == pytest.approx(0.5)
    assert tb.zero_cross(g, np.array([-1, 0, 1, 1, 1]), 2.0) == pytest.approx(1.0)


def test_ladder_round_steps():
    lad = tb.spot_ladder(7376.86)
    assert lad[0] % 25 == 0 and lad[-1] % 25 == 0
    assert lad[0] <= 7376.86 * 0.88 and lad[-1] >= 7376.86 * 1.12
    assert np.allclose(np.diff(lad), 25.0)


def test_buckets_sum_to_total_and_match_profile_engine():
    ch = _book()
    out = tb.tenor_gamma(ch, 7376.86, BOOK)
    s = sum(out["buckets"][k]["at_spot"] for k in tb.BUCKET_KEYS)
    assert s == pytest.approx(out["total"]["at_spot"], abs=1e-3)
    curve_sum = np.sum([out["buckets"][k]["curve"] for k in tb.BUCKET_KEYS], axis=0)
    assert np.allclose(curve_sum, out["total"]["curve"], atol=1e-3)
    # same convention as get_profile's engine: identical value at spot
    prof = greek_profiles(
        ch.rename(columns={"expiry": "expiration"}).assign(
            opt_kind=np.where(ch["cp"] == "C", "call", "put")
        ),
        7376.86,
        span=0.001,
        n_points=3,
        ref=BOOK,
        greeks=("gamma",),
    )
    assert out["total"]["at_spot"] == pytest.approx(prof["gamma"]["all"][1], rel=1e-4)
    assert out["max_expiry"] == "2026-12-18"
    assert out["note"] == tb.DISCLAIMER


def test_put_heavy_book_is_negative_with_shares():
    out = tb.tenor_gamma(_book(), 7350.0, BOOK)
    assert out["total"]["at_spot"] < 0
    shares = [out["buckets"][k]["share_pct"] for k in tb.BUCKET_KEYS]
    assert sum(shares) == pytest.approx(100.0, abs=0.5)


def test_expired_and_bad_rows_are_dropped():
    ch = pd.concat(
        [
            _book(),
            pd.DataFrame(
                [
                    {
                        "strike": 7400.0,
                        "cp": "C",
                        "iv": 0.2,
                        "oi": 99999,
                        "expiry": BOOK,
                    },  # settled
                    {"strike": 7400.0, "cp": "C", "iv": 0.0, "oi": 5, "expiry": date(2026, 11, 9)},
                    {"strike": 7400.0, "cp": "P", "iv": 0.2, "oi": 0, "expiry": date(2026, 11, 9)},
                ]
            ),
        ]
    )
    assert tb.tenor_gamma(ch, 7376.0, BOOK)["n_contracts"] == len(_book())


def test_vega_conventions():
    v = tb.tenor_vega(_book(), 7376.86, BOOK)
    for k in tb.BUCKET_KEYS:
        b = v["buckets"][k]
        assert b["naive"] == pytest.approx(b["call_vega"] - b["put_vega"], abs=2e-3)
        assert b["flipped"] == pytest.approx(-b["naive"], abs=2e-3)
        assert b["short_all"] == pytest.approx(-(b["call_vega"] + b["put_vega"]), abs=2e-3)
    assert v["total"]["naive"] < 0  # 3x the put OI
    # longer tenor carries more vega per contract
    assert abs(v["buckets"]["gt_1m"]["naive"]) > abs(v["buckets"]["1d"]["naive"])
    assert len(v["by_expiry"]) == 4


def test_empty_inputs():
    assert tb.tenor_gamma(pd.DataFrame(), 7000.0, BOOK) == {}
    assert tb.tenor_vega(pd.DataFrame(), 7000.0, BOOK) == {}
    assert tb.tenor_gamma(_book(), float("nan"), BOOK) == {}
