"""The "Yesterday" line and the Monday scorecard — levels only, no odds."""

from __future__ import annotations

from datetime import date

from trading_intel.synthesis.daily_brief_render import _map_grade_html


def _row(d: date, **over) -> dict:
    base = {
        "date": d, "floor": 7600.0, "cap": 7800.0,
        "bucket": "between", "close": 7690.0, "inside_range70": True,
        "floor_touched": True, "floor_held": True, "cap_touched": False,
        "cap_held": None, "levels_firm": True, "wall_broke": False,
    }
    base.update(over)
    return base


def test_no_history_renders_nothing():
    assert _map_grade_html({}) == ""
    assert _map_grade_html({"map_history": []}) == ""


def test_yesterday_line_in_plain_words():
    html = _map_grade_html({"map_history": [_row(date(2026, 10, 26))]})
    assert "Yesterday" in html and "Oct 26" in html
    assert "Closed between the floor and the cap" in html
    assert "the floor was tested and held" in html
    assert "inside the likely range" in html
    assert "\U0001F7E2" in html


def test_yesterday_line_when_the_floor_gave_way():
    row = _row(date(2026, 10, 26), bucket="below", close=7540.0,
               inside_range70=False, floor_held=False, wall_broke=True)
    html = _map_grade_html({"map_history": [row]})
    assert "Closed below the 7,600 floor" in html
    assert "the floor gave way" in html
    assert "outside the likely range" in html
    assert "\U0001F534" in html


def test_yesterday_line_when_the_cap_was_taken_out():
    row = _row(date(2026, 10, 26), bucket="above", close=7860.0,
               floor_touched=False, floor_held=None,
               cap_touched=True, cap_held=False, wall_broke=True)
    html = _map_grade_html({"map_history": [row]})
    assert "Closed above the 7,800 cap" in html
    assert "the cap was taken out" in html


def test_yesterday_line_when_nothing_was_reached():
    row = _row(date(2026, 10, 26), floor_touched=False, floor_held=None)
    assert "neither level was reached" in _map_grade_html({"map_history": [row]})


def test_no_odds_language_survives():
    html = _map_grade_html({"map_history": [_row(date(2026, 10, 26))]})
    for banned in ("card leaned", "%", "modal", "at 64"):
        assert banned not in html, banned


def test_ungraded_row_is_neutral():
    row = _row(date(2026, 10, 26), bucket=None)
    html = _map_grade_html({"map_history": [row]})
    assert "Not graded" in html and "⚪" in html


def test_scorecard_only_on_mondays_and_only_with_enough_days():
    rows = [_row(date(2026, 10, 19)) for _ in range(8)]
    assert "How the map has been doing" not in _map_grade_html({"map_history": rows})
    assert "How the map has been doing" in _map_grade_html(
        {"map_history": rows, "is_monday": True}
    )
    few = [_row(date(2026, 10, 19)) for _ in range(3)]
    assert "How the map has been doing" not in _map_grade_html(
        {"map_history": few, "is_monday": True}
    )


def test_scorecard_flags_a_range_that_is_too_wide():
    rows = [_row(date(2026, 10, 19), inside_range70=True) for _ in range(10)]
    html = _map_grade_html({"map_history": rows, "is_monday": True})
    assert "100% of closes landed inside it" in html
    assert "the range looks too wide" in html


def test_scorecard_reports_the_how_solid_audit():
    rows = [_row(date(2026, 10, 19), levels_firm=False, wall_broke=True,
                 bucket="below") for _ in range(5)]
    rows += [_row(date(2026, 10, 20), levels_firm=True, wall_broke=False)
             for _ in range(5)]
    html = _map_grade_html({"map_history": rows, "is_monday": True})
    assert "days flagged shaky broke 100%" in html
    assert "working" in html


def test_scorecard_says_when_the_flag_is_not_earning_its_place():
    rows = [_row(date(2026, 10, 19), levels_firm=False, wall_broke=False)
            for _ in range(5)]
    rows += [_row(date(2026, 10, 20), levels_firm=True, wall_broke=True,
                  bucket="below") for _ in range(5)]
    assert "not earning its place" in _map_grade_html(
        {"map_history": rows, "is_monday": True}
    )
