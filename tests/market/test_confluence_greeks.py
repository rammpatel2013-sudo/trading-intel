"""Confluence layer (a): greek maths on synthetic chains with known answers."""

from __future__ import annotations

import math

import pytest

from trading_intel.market import confluence_greeks as cg
from trading_intel.market.confluence_greeks import GreekRow


def _fd(f, x: float, h: float) -> float:
    return (f(x + h) - f(x - h)) / (2 * h)


def test_speed_matches_finite_difference_of_gamma_and_has_the_plus_one() -> None:
    s, k, sig, t = 7400.0, 7300.0, 0.2, 10 / 365
    fd = _fd(lambda x: cg.bs_gamma(x, k, sig, t), s, 0.5)
    assert cg.bs_speed(s, k, sig, t) == pytest.approx(fd, rel=1e-5)
    # without the "+1" the formula is wrong by Γ/S
    d1, _ = cg.d1_d2(s, k, sig, t)
    no_plus_one = -(cg.bs_gamma(s, k, sig, t) / s) * (d1 / (sig * math.sqrt(t)))
    assert abs(no_plus_one - fd) > 1e-3 * abs(cg.bs_gamma(s, k, sig, t) / s)


def test_speed_sign_flips_across_the_strike() -> None:
    k, sig, t = 7000.0, 0.2, 7 / 365
    # spot below the gamma peak: gamma rising with spot -> speed > 0; above: < 0
    assert cg.bs_speed(6900.0, k, sig, t) > 0
    assert cg.bs_speed(7100.0, k, sig, t) < 0


def test_vomma_matches_finite_difference_of_vega() -> None:
    s, k, sig, t = 7400.0, 7000.0, 0.22, 20 / 365
    fd = _fd(lambda v: cg.bs_vega(s, k, v, t), sig, 1e-4)
    assert cg.bs_vomma(s, k, sig, t) == pytest.approx(fd, rel=1e-5)


def test_vomma_peak_of_a_single_option_is_its_strike() -> None:
    rows = [GreekRow(7000.0, "P", 7, 0.22, 10_000)]
    v = cg.vomma_by_strike(rows, 7400.0)
    assert list(v) == [7000.0] and v[7000.0] > 0
    lv = cg.greek_levels(rows, 7400.0)
    peaks = [x for x in lv if x["kind"] == "vomma_peak"]
    assert [p["level"] for p in peaks] == [7000.0] and peaks[0]["bucket"] == "<=7d"


def test_vomma_peak_picks_the_larger_cluster() -> None:
    rows = [GreekRow(k, "P", 7, 0.2, oi) for k, oi in ((7000.0, 5000), (7100.0, 20_000),
                                                      (7200.0, 4000))]
    peaks = cg.local_peaks(cg.vomma_by_strike(rows, 7400.0))
    assert peaks[0][0] == 7100.0


def test_gamma_zero_of_a_symmetric_book_is_the_middle() -> None:
    # calls at 7100 (+) and puts at 6900 (−), same OI and IV: the two gammas are equal
    # where |d1| matches, i.e. S = sqrt(K1*K2) * exp(-sigma^2 T / 2) (~6995.5, not 7000)
    rows = [GreekRow(7100.0, "C", 10, 0.2, 10_000), GreekRow(6900.0, "P", 10, 0.2, 10_000)]
    ladder = cg.spot_ladder(7000.0, 0.06, 241)
    zs = cg.zero_crossings(ladder, cg.dealer_gamma_curve(rows, ladder))
    exact = math.sqrt(7100 * 6900) * math.exp(-0.5 * 0.04 * 10 / 365)
    assert len(zs) == 1 and zs[0] == pytest.approx(exact, abs=0.5)


def test_greek_levels_label_buckets_and_skip_edge_extremes() -> None:
    rows = [GreekRow(7100.0, "C", 5, 0.2, 10_000), GreekRow(6900.0, "P", 5, 0.2, 10_000),
            GreekRow(7300.0, "C", 20, 0.2, 5000)]
    lv = cg.greek_levels(rows, 7000.0)
    assert {x["bucket"] for x in lv} >= {"<=7d", "8-30d", "0-45d"}
    ladder = cg.spot_ladder(7000.0)
    for x in lv:
        if x["kind"] == "speed_extreme":
            assert ladder[0] < x["level"] < ladder[-1]


def test_zero_crossings_interpolates() -> None:
    assert cg.zero_crossings([0, 1, 2], [-1.0, 1.0, 3.0]) == [0.5]
    assert cg.zero_crossings([0, 1], [1.0, 2.0]) == []


def test_strike_for_delta_round_trips() -> None:
    s, sig, t = 7378.9, 0.2657, 7 / 365
    k = cg.strike_for_delta(s, -0.25, sig, t, "P")
    assert cg.bs_delta(s, k, sig, t, "P") == pytest.approx(-0.25, abs=1e-6)
    kc = cg.strike_for_delta(s, 0.22, 0.19, t, "C")
    assert cg.bs_delta(s, kc, 0.19, t, "C") == pytest.approx(0.22, abs=1e-6)
    assert cg.strike_for_delta(s, 1.2, sig, t, "C") is None


def test_invalid_rows_are_ignored() -> None:
    rows = [GreekRow(7000.0, "C", 5, None, 100), GreekRow(7000.0, "C", 5, 0.2, 0)]
    assert cg.greek_levels(rows, 7000.0) == []


def test_vol_shock_peak_ignores_oi_and_is_info_only() -> None:
    # tiny OI on the far put, huge OI near the money: per-contract vomma picks by
    # spot/vol/time only, so the OI size makes no difference
    rows = [GreekRow(k, "P", 7, 0.30, 1 if k == 6980 else 50_000) for k in (6900.0, 6980.0, 7300.0)]
    peaks = dict((w, k) for w, k, _ in cg.vol_shock_peaks(rows, 7378.9))
    single = {k: cg.bs_vomma(7378.9, k, 0.30, 7 / 365) for k in (6900.0, 6980.0, 7300.0)}
    assert peaks["put"] == max(single, key=single.get)
    lv = [x for x in cg.greek_levels(rows, 7378.9) if x["kind"] == "vol_shock_peak"]
    assert lv and all(x["info"] and x["strength"] == 0.0 and "(info)" in x["label"] for x in lv)
