"""Grading the MAP card against the tape, and the Monday scorecard roll-up."""

from __future__ import annotations

import pytest

from trading_intel.market.grading import grade_session, scorecard

_LEVELS = {"floor": 5900.0, "cap": 6100.0, "range70_lo": 5930.0, "range70_hi": 6095.0}


def _g(high, low, close, **over):
    return grade_session(**{**_LEVELS, **over}, high=high, low=low, close=close)


def test_bucket_below_between_above():
    assert _g(6050, 5880, 5870)["bucket"] == "below"
    assert _g(6050, 5950, 6000)["bucket"] == "between"
    assert _g(6150, 5990, 6120)["bucket"] == "above"


def test_bucket_boundaries_are_inclusive_of_between():
    """Closing exactly ON a wall is not a break."""
    assert _g(6100, 5900, 5900.0)["bucket"] == "between"
    assert _g(6100, 5900, 6100.0)["bucket"] == "between"
    assert _g(6100, 5900, 5900.0)["wall_broke"] is False


def test_touched_but_held_is_distinct_from_broke():
    r = _g(6050, 5880, 5960)  # dipped through the floor, closed back inside
    assert r["floor_touched"] is True
    assert r["floor_held"] is True
    assert r["wall_broke"] is False
    assert r["bucket"] == "between"


def test_touched_and_lost():
    r = _g(6050, 5880, 5870)
    assert r["floor_touched"] is True
    assert r["floor_held"] is False
    assert r["wall_broke"] is True


def test_held_is_none_when_never_tested():
    """A wall that was never traded through gets no held/not-held verdict."""
    r = _g(6050, 5950, 6000)
    assert r["floor_touched"] is False
    assert r["floor_held"] is None
    assert r["cap_touched"] is False
    assert r["cap_held"] is None


def test_cap_touched_and_held():
    r = _g(6150, 5990, 6050)
    assert r["cap_touched"] is True and r["cap_held"] is True


def test_inside_range70():
    assert _g(6050, 5950, 6000)["inside_range70"] is True
    assert _g(6050, 5880, 5910)["inside_range70"] is False


def test_range70_tolerates_reversed_bounds():
    r = grade_session(floor=None, cap=None, range70_lo=6095.0, range70_hi=5930.0,
                      high=6050, low=5950, close=6000)
    assert r["inside_range70"] is True


def test_missing_inputs_grade_to_none_not_false():
    r = grade_session(floor=None, cap=None, range70_lo=None, range70_hi=None,
                      high=None, low=None, close=6000)
    assert all(v is None for v in r.values())
    assert grade_session(floor=5900.0, cap=6100.0, range70_lo=None, range70_hi=None,
                         high=6050, low=5950, close=None)["bucket"] is None


def _row(bucket, *, firm=None, broke=False, in70=True, ft=False, fh=None, ct=False, ch=None):
    return {"bucket": bucket, "levels_firm": firm, "wall_broke": broke,
            "inside_range70": in70, "floor_touched": ft, "floor_held": fh,
            "cap_touched": ct, "cap_held": ch}


def test_scorecard_empty():
    card = scorecard([])
    assert card["n"] == 0 and card["range70_rate"] is None


def test_scorecard_skips_ungraded_rows():
    card = scorecard([_row("between"), {"bucket": None}, {}])
    assert card["n"] == 1


def test_scorecard_counts_buckets_and_calibration():
    rows = [_row("between", in70=True) for _ in range(7)]
    rows += [_row("below", in70=False), _row("above", in70=False), _row("between", in70=True)]
    card = scorecard(rows)
    assert card["n"] == 10
    assert card["buckets"] == {"below": 1, "between": 8, "above": 1}
    assert card["range70_rate"] == pytest.approx(0.8)
    assert card["range70_expected"] == 0.70


def test_scorecard_wall_tests_and_holds():
    rows = [
        _row("between", ft=True, fh=True),
        _row("between", ft=True, fh=True),
        _row("below", ft=True, fh=False, broke=True),
        _row("between", ct=True, ch=True),
    ]
    card = scorecard(rows)
    assert (card["floor_tests"], card["floor_holds"]) == (3, 2)
    assert (card["cap_tests"], card["cap_holds"]) == (1, 1)


def test_scorecard_fragility_verdict_when_soft_breaks_more():
    rows = [_row("below", firm=False, broke=True) for _ in range(4)]
    rows += [_row("between", firm=False, broke=False)]
    rows += [_row("between", firm=True, broke=False) for _ in range(5)]
    card = scorecard(rows)
    assert card["soft"]["n"] == 5 and card["soft"]["rate"] == pytest.approx(0.8)
    assert card["firm"]["n"] == 5 and card["firm"]["rate"] == 0.0
    assert "working" in card["fragility_verdict"]


def test_scorecard_fragility_verdict_when_flag_is_useless():
    rows = [_row("between", firm=False, broke=False) for _ in range(5)]
    rows += [_row("below", firm=True, broke=True) for _ in range(5)]
    card = scorecard(rows)
    assert "not earning its place" in card["fragility_verdict"]


def test_scorecard_withholds_verdict_on_thin_samples():
    """Fewer than 5 of either kind is not enough to judge the flag."""
    rows = [_row("below", firm=False, broke=True) for _ in range(3)]
    rows += [_row("between", firm=True, broke=False) for _ in range(9)]
    assert scorecard(rows)["fragility_verdict"] is None
