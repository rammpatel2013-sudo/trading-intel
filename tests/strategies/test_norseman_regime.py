"""Norseman event detection — decision points fire once, with stable keys."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from trading_intel.market import norseman as nm
from trading_intel.strategies import norseman_regime as s


def _state(**kw) -> s.NorsemanState:
    base = s.NorsemanState(
        as_of=date(2026, 9, 25), spx_close=7743.41, spx_high=7750.0, spx_low=7700.0,
        ath=7816.70, ath_date=date(2026, 8, 13), line=7035.03, line_in_force=7035.03,
        dist_to_line=7743.41 / 7035.03 - 1, week_close=True,
        last_test=nm.TestEpisode(date(2026, 3, 27), date(2026, 3, 30), 6316.91, 6302.05),
        session=124, clock_open_date=date(2026, 10, 1), clock_open=False,
        count_start=date(2025, 5, 2), violations=[], tapped_today=False,
        dials=nm.DialsRead(price_high_date=date(2026, 8, 13), rails=[]),
        author={"bull_bear_line": 7035.03, "target": 7930.0, "assess_zone": 8125.0,
                "as_of": date(2026, 9, 27)},
        author_line_ours=7035.03,
    )
    return replace(base, **kw)


def _kinds(st: s.NorsemanState) -> list[str]:
    return [e["kind"] for e in s.detect_events(st)]


def test_issue_70_state_is_only_the_weekly_grade() -> None:
    assert _kinds(_state()) == ["WEEKLY_GRADE"]
    grade = s.weekly_grade(_state())
    assert "7,035.03" in grade and "124/128" in grade and "opens 10/1" in grade


def test_clock_open_fires() -> None:
    assert "CLOCK_OPEN" in _kinds(_state(session=128, clock_open=True, week_close=False))


def test_violation_on_weekly_close_below_line() -> None:
    st = _state(spx_close=7000.0, dist_to_line=7000 / 7035.03 - 1)
    ev = [e for e in s.detect_events(st) if e["kind"] == "VIOLATION"]
    assert ev and ev[0]["severity"] == "critical"
    # mid-week close below the line is NOT a violation
    assert "VIOLATION" not in _kinds(replace(st, week_close=False))


def test_targets_and_proximity() -> None:
    assert "TARGET" in _kinds(_state(spx_high=7931.0, week_close=False))
    near = _state(spx_close=7150.0, dist_to_line=7150 / 7035.03 - 1, week_close=False)
    ev = [e for e in s.detect_events(near) if e["kind"] == "PROXIMITY"]
    assert ev and ev[0]["key"] == "prox2:7035"


def test_assess_needs_core_rail_not_just_small_caps() -> None:
    iwm = nm.RailRead("IWM (small caps)", date(2026, 9, 1), 300, 280, -0.07, True, 20)
    rsp = nm.RailRead("RSP (average stock)", date(2026, 8, 19), 223.4, 215, -0.04, True, 30)
    hi = date(2026, 10, 2)
    only_iwm = _state(as_of=hi, ath_date=hi, week_close=False,
                      dials=nm.DialsRead(price_high_date=hi, rails=[iwm]))
    assert "ASSESS_START" not in _kinds(only_iwm)
    both = replace(only_iwm, dials=nm.DialsRead(price_high_date=hi, rails=[iwm, rsp]))
    kinds = _kinds(both)
    assert "ASSESS_START" in kinds and "ASSESS_MATURE" in kinds


def test_line_mismatch_flag() -> None:
    assert "LINE_MISMATCH" in _kinds(_state(author_line_ours=6987.06, week_close=False))
