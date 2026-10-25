"""The MAP builder's own maths: close horizon, source pick, fragility legs."""

from __future__ import annotations

from datetime import datetime

import pytest

from trading_intel.market import map_block as mbx
from trading_intel.synthesis import gamma_map as gm


def _book(ivs: tuple[float, ...] = (0.14, 0.16)) -> mbx.Book:
    rows = []
    for dte in (0, 7):
        for k in range(5700, 6301, 25):
            rows.append((float(k), "C", dte, 5_000, 0.3, 0.0, ivs[0], 1.0, 6.0, 4.0))
            rows.append((float(k), "P", dte, 5_000, -0.3, 0.0, ivs[1], 1.0, 3.0, 9.0))
    return mbx.build_book(rows, dte_max=7)


def test_hours_to_close_before_and_after_the_bell():
    morning = datetime(2026, 10, 27, 8, 45)
    assert gm._hours_to_close(morning) == pytest.approx(7.25, abs=1e-6)
    # After 16:00 it rolls to the NEXT close rather than going negative.
    assert gm._hours_to_close(datetime(2026, 10, 27, 17, 0)) == pytest.approx(23.0, abs=1e-6)
    assert gm._hours_to_close(datetime(2026, 10, 27, 16, 0)) > 0


def test_fragility_threshold_uses_quarter_sigma_when_it_exceeds_ten_points():
    """Build plan §6: tol = max(10 pts, ¼ of the day's 1σ)."""
    book = _book()
    quiet = gm._fragility(book, 6000.0, atm_iv=0.05)   # 1σ ≈ 19 pts -> ¼ ≈ 5 -> floor wins
    busy = gm._fragility(book, 6000.0, atm_iv=0.30)    # 1σ ≈ 113 pts -> ¼ ≈ 28
    assert quiet["tol"] == pytest.approx(10.0)
    assert busy["tol"] == pytest.approx(0.25 * 6000.0 * 0.30 / (252 ** 0.5))
    assert busy["tol"] > quiet["tol"]


def test_fragility_falls_back_to_the_floor_without_iv():
    assert gm._fragility(_book(), 6000.0, atm_iv=None)["tol"] == pytest.approx(10.0)


def test_fragility_scores_walls_and_flip_separately():
    """Review fix #1 — averaging the two would hide a pinned wall + skittish flip."""
    frag = gm._fragility(_book(), 6000.0, atm_iv=0.14)
    names = [leg["name"] for leg in frag["legs"]]
    assert names == ["walls", "flip"]
    for leg in frag["legs"]:
        assert leg["move"] is None or leg["move"] >= 0


def test_fragility_overall_is_firm_only_when_every_leg_is():
    frag = gm._fragility(_book(), 6000.0, atm_iv=0.14)
    legs = [l["firm"] for l in frag["legs"] if l["firm"] is not None]
    if legs:
        assert frag["firm"] == all(legs)
        assert frag["soft_parts"] == [l["name"] for l in frag["legs"] if l["firm"] is False]


def test_fragility_calibrating_until_enough_graded_days():
    book = _book()
    early = gm._fragility(book, 6000.0, atm_iv=0.14, graded_sessions=5)
    mature = gm._fragility(book, 6000.0, atm_iv=0.14, graded_sessions=120)
    assert early["calibrating"] is True
    assert "calibrating" in early["label"]
    assert mature["calibrating"] is False
    assert "calibrating" not in mature["label"]
    # The verdict itself is unchanged — only how loudly it is claimed.
    assert early["firm"] == mature["firm"]


def test_fragility_empty_book_is_not_a_verdict():
    frag = gm._fragility(mbx.build_book([], dte_max=7), 6000.0, atm_iv=0.14)
    assert frag["firm"] is None
    assert frag["label"] == "levels n/a"
    assert frag["max_move"] is None
