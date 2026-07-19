"""Tests for the pure EM-break outcome engine (no I/O)."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.backtest.em_break import (
    Outcome,
    bars_from_rows,
    evaluate_outcome,
    summarize,
    summarize_by_bucket,
)


def _path(rows):
    return bars_from_rows(rows)


def test_win_at_target():
    path = _path(
        [
            (date(2026, 7, 1), 104, 99, 103),
            (date(2026, 7, 2), 111, 105, 110),  # high crosses 110
        ]
    )
    oc = evaluate_outcome(100, 110, 95, path)
    assert oc.result == "win"
    assert oc.exit_price == 110
    assert oc.days_held == 2
    assert oc.r_multiple == pytest.approx(2.0)  # (110-100)/(100-95)


def test_loss_at_stop():
    path = _path([(date(2026, 7, 1), 102, 94, 96)])  # low crosses 95
    oc = evaluate_outcome(100, 110, 95, path)
    assert oc.result == "loss"
    assert oc.r_multiple == pytest.approx(-1.0)
    assert oc.days_held == 1


def test_open_marks_to_last_close():
    path = _path(
        [
            (date(2026, 7, 1), 103, 98, 102),
            (date(2026, 7, 2), 106, 100, 105),
        ]
    )
    oc = evaluate_outcome(100, 110, 95, path)
    assert oc.result == "open"
    assert oc.exit_price == 105
    assert oc.r_multiple == pytest.approx(1.0)  # (105-100)/5


def test_same_bar_touch_resolves_conservatively_to_stop():
    path = _path([(date(2026, 7, 1), 112, 94, 100)])  # touches both target and stop
    oc = evaluate_outcome(100, 110, 95, path)
    assert oc.result == "loss"


def test_invalid_geometry_and_empty_return_none():
    good = _path([(date(2026, 7, 1), 111, 99, 110)])
    assert evaluate_outcome(100, 95, 90, good) is None  # target below entry
    assert evaluate_outcome(100, 110, 105, good) is None  # stop above entry
    assert evaluate_outcome(100, 110, 95, []) is None  # empty path


def test_max_days_truncation_keeps_it_open():
    path = _path(
        [
            (date(2026, 7, 1), 103, 98, 102),
            (date(2026, 7, 2), 111, 105, 110),  # would win on day 2
        ]
    )
    oc = evaluate_outcome(100, 110, 95, path, max_days=1)
    assert oc.result == "open"
    assert oc.days_held == 1


def test_summarize_hit_rate_and_expectancy():
    wins = [
        evaluate_outcome(100, 110, 95, _path([(date(2026, 7, 2), 111, 105, 110)])),
        evaluate_outcome(100, 110, 95, _path([(date(2026, 7, 2), 112, 106, 111)])),
    ]
    loss = [evaluate_outcome(100, 110, 95, _path([(date(2026, 7, 2), 101, 94, 96)]))]
    s = summarize([*wins, *loss])
    assert s["n_closed"] == 3
    assert s["wins"] == 2
    assert s["hit_rate"] == pytest.approx(2 / 3)
    assert s["avg_r"] == pytest.approx((2.0 + 2.0 - 1.0) / 3)


def test_summarize_by_bucket_splits_on_conviction():
    win = evaluate_outcome(100, 110, 95, _path([(date(2026, 7, 2), 111, 105, 110)]))
    loss = evaluate_outcome(100, 110, 95, _path([(date(2026, 7, 2), 101, 94, 96)]))
    buckets = summarize_by_bucket([(90.0, win), (60.0, loss)], edges=(70.0, 85.0))
    assert buckets["[85,inf]"]["wins"] == 1
    assert buckets["[-inf,70)"]["losses"] == 1
