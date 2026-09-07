"""Regression tests for the 2026-09-07 daily-brief errors.

Each test pins one defect found in the 2026-09-07 edition. Named for the symptom
so a future failure explains itself.
"""
from __future__ import annotations

from datetime import date

from trading_intel.market.gex_transition import eod_gex_series
from trading_intel.synthesis.daily_brief import (
    _direction_agrees,
    _letter_age,
    _mentions_subject,
    _prev_session,
    _trading_days_between,
    _trading_sessions,
)
from trading_intel.synthesis.daily_brief_render import _declutter

# 2026-09-05 Sat · 09-06 Sun · 09-07 Mon = Labor Day. greeks_snapshot wrote a row
# on every one of them, with spot frozen and greeks re-derived off a stale chain.
_SNAPSHOTS = [
    {"date": "2026-09-03", "spot": 7748.27, "gex_flip": 7685.1, "gex_total": 213.0},
    {"date": "2026-09-03", "spot": 7748.27, "gex_flip": 7685.1, "gex_total": 213.0},
    {"date": "2026-09-04", "spot": 7747.71, "gex_flip": 7698.3, "gex_total": 284.0},
    {"date": "2026-09-04", "spot": 7717.81, "gex_flip": 7701.0, "gex_total": 24.7},
    {"date": "2026-09-05", "spot": 7717.81, "gex_flip": 7701.8, "gex_total": 24.7},
    {"date": "2026-09-06", "spot": 7718.60, "gex_flip": 7704.2, "gex_total": 8.9},
    {"date": "2026-09-07", "spot": 7718.60, "gex_flip": 7708.3, "gex_total": 0.7},
]


def test_board_never_reports_a_weekend_or_holiday_session():
    """The board stamped its SPX/SPY/QQQ rows 2026-09-06 — a Sunday."""
    out = _trading_sessions(_SNAPSHOTS)
    assert [str(r["session"]) for r in out] == ["2026-09-03", "2026-09-04"]


def test_one_row_per_session_not_per_snapshot():
    """rows[-10:] was labelled "the last 10 sessions"; it was ~3 days of snapshots."""
    out = _trading_sessions(_SNAPSHOTS)
    assert len(out) == 2
    # last snapshot of the day wins (the EOD read)
    assert out[-1]["gex_total"] == 24.7


def test_gex_transition_series_excludes_closed_days():
    """Net GEX 213 -> 0.7 was weekend drift entering the z-score as real moves."""
    ser = eod_gex_series(_SNAPSHOTS)
    assert [str(r["date"]) for r in ser] == ["2026-09-03", "2026-09-04"]


def test_prev_session_backs_over_labor_day_weekend():
    assert _prev_session(date(2026, 9, 7)) == date(2026, 9, 4)


def test_trading_days_between_skips_closed_days():
    assert _trading_days_between(date(2026, 9, 4), date(2026, 9, 8)) == 1


def test_stale_letter_is_not_fresh():
    """An Aug 13 daily plan was published under "Doc's read into today"."""
    age = _letter_age({"as_of": "2026-08-20"}, date(2026, 9, 7))
    assert age["fresh"] is False
    assert age["age"] == 11
    assert "sessions old" in age["label"]


def test_todays_letter_is_fresh():
    age = _letter_age({"as_of": "2026-09-04"}, date(2026, 9, 7))
    assert age["fresh"] is True


def test_undated_letter_is_never_fresh():
    assert _letter_age({}, date(2026, 9, 7))["fresh"] is False


def test_direction_must_match_the_rationale():
    """FMX shipped tagged Bear on "significant future growth potential"."""
    bullish_text = ("Lack of volume data post-August 3 listing, significant "
                    "future growth potential.")
    assert _direction_agrees(-1.0, bullish_text) is False
    assert _direction_agrees(1.0, bullish_text) is True


def test_direction_defers_to_tagger_when_text_is_neutral():
    assert _direction_agrees(-1.0, "Company reported results.") is True


def test_rationale_about_another_company_is_rejected():
    """AAPL shipped Bull on "Abbott's settlements ... impact Apple's stock"."""
    assert _mentions_subject(
        "AAPL", "Abbott's settlements are expected to positively impact Apple's stock"
    ) is False
    assert _mentions_subject(
        "CYTK", "Cytokinetics is the primary focus of the document."
    ) is True


def test_ladder_labels_do_not_collide():
    """put wall 7,700 / flip 7,704 / spot 7,719 landed inside 13px of each other."""
    out = _declutter([39.7, 243.5, 240.6, 230.8, 200.5, 261.2])
    ordered = sorted(out)
    assert all(b - a >= 11.9 for a, b in zip(ordered, ordered[1:]))
    # relative order must survive the nudging
    assert out.index(max(out)) == 5 and out.index(min(out)) == 0
