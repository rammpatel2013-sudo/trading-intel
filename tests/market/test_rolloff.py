"""Gamma roll-off from the GEX term structure."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.market.rolloff import build_rolloff, is_monthly_opex, week_end

MON = date(2026, 10, 26)


def _term() -> list[dict]:
    return [
        {"expiration": "2026-10-26", "dte": 0, "gex": 400.0},
        {"expiration": "2026-10-28", "dte": 2, "gex": 200.0},
        {"expiration": "2026-10-30", "dte": 4, "gex": 300.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": 100.0},  # next month
    ]


def test_week_end_is_this_weeks_friday():
    assert week_end(MON) == date(2026, 10, 30)
    assert week_end(date(2026, 10, 30)) == date(2026, 10, 30)  # Friday stays
    assert week_end(date(2026, 10, 31)) == date(2026, 11, 6)  # Saturday rolls on


def test_is_monthly_opex_third_friday():
    assert is_monthly_opex(date(2026, 10, 16)) is True
    assert is_monthly_opex(date(2026, 10, 23)) is False


def test_steps_stop_at_this_weeks_friday():
    r = build_rolloff(_term(), today=MON)
    assert r["found"] is True
    assert [s["expiration"] for s in r["steps"]] == [
        "2026-10-26", "2026-10-28", "2026-10-30"
    ]  # the Nov expiry is not part of this week's roll-off


def test_remaining_steps_down_to_whats_left_after_friday():
    r = build_rolloff(_term(), today=MON)
    assert r["total"] == pytest.approx(1000.0)
    assert [s["remaining_after"] for s in r["steps"]] == pytest.approx([600.0, 400.0, 100.0])
    # What survives the week is the November pole.
    assert r["steps"][-1]["remaining_after"] == pytest.approx(100.0)


def test_week_share_and_biggest_expiry():
    r = build_rolloff(_term(), today=MON)
    assert r["week_share"] == pytest.approx(0.9)
    assert r["biggest"]["expiration"] == "2026-10-26"
    assert r["biggest"]["share"] == pytest.approx(0.4)
    assert r["biggest"]["is_this_week"] is True


def test_biggest_can_sit_outside_this_week():
    term = [
        {"expiration": "2026-10-28", "dte": 2, "gex": 100.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": 900.0},
    ]
    r = build_rolloff(term, today=MON)
    assert r["biggest"]["expiration"] == "2026-11-20"
    assert r["biggest"]["is_this_week"] is False
    assert r["week_share"] == pytest.approx(0.1)


def test_expiring_today_is_surfaced_for_opex_day():
    r = build_rolloff(_term(), today=MON)
    assert r["expiring_today"]["share"] == pytest.approx(0.4)
    later = build_rolloff(_term(), today=date(2026, 10, 27))
    assert later["expiring_today"] is None


def test_opex_flag_rides_along():
    opex = date(2026, 10, 16)
    r = build_rolloff([{"expiration": "2026-10-16", "dte": 0, "gex": 500.0}], today=opex)
    assert r["is_opex"] is True
    assert build_rolloff(_term(), today=MON)["is_opex"] is False


def test_past_expiries_and_junk_rows_are_dropped():
    term = _term() + [
        {"expiration": "2026-10-01", "dte": -25, "gex": 999.0},  # already gone
        {"expiration": None, "dte": 1, "gex": 50.0},
        {"expiration": "2026-10-29", "dte": 3, "gex": None},
        {"expiration": "not-a-date", "dte": 1, "gex": 10.0},
        {"expiration": "2026-10-29", "dte": 3, "gex": "abc"},
    ]
    r = build_rolloff(term, today=MON)
    assert r["total"] == pytest.approx(1000.0)
    assert len(r["steps"]) == 3


def test_negative_gamma_book_rolls_off_too():
    term = [
        {"expiration": "2026-10-28", "dte": 2, "gex": -600.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": -400.0},
    ]
    r = build_rolloff(term, today=MON)
    assert r["total"] == pytest.approx(-1000.0)
    assert r["steps"][0]["remaining_after"] == pytest.approx(-400.0)
    assert r["steps"][0]["share"] == pytest.approx(0.6)


def test_empty_term_is_not_found():
    r = build_rolloff([], today=MON)
    assert r["found"] is False and r["steps"] == []
    assert build_rolloff(None, today=MON)["found"] is False
