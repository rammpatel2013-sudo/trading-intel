"""Tests for the dashboard freshness helpers."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

_UTC = timezone(timedelta(0))  # explicit offset avoids the datetime.UTC (3.11) vs timezone.utc split

from trading_intel.dashboard.freshness import (
    age,
    format_et,
    freshness_caption,
    staleness,
)


def test_format_et_datetime_date_none():
    assert format_et(datetime(2026, 5, 26, 16, 35)) == "2026-05-26 16:35 ET"
    assert format_et(date(2026, 5, 26)) == "2026-05-26"
    assert format_et(None) == "no data yet"


def test_format_et_aware_converts_to_eastern():
    # 20:35 UTC -> 16:35 EDT (summer, UTC-4).
    aware = datetime(2026, 5, 26, 20, 35, tzinfo=_UTC)
    assert format_et(aware) == "2026-05-26 16:35 ET"


def test_freshness_caption():
    assert freshness_caption(date(2026, 5, 26)) == "Last data pulled: 2026-05-26"
    assert freshness_caption(None, label="Updated") == "Updated: no data yet"


def test_age_and_none():
    now = datetime(2026, 5, 26, 12, 0)
    assert age(datetime(2026, 5, 26, 9, 0), now=now) == timedelta(hours=3)
    assert age(None, now=now) is None
    # date-only anchors at midnight.
    assert age(date(2026, 5, 26), now=now) == timedelta(hours=12)


def test_staleness_states():
    now = datetime(2026, 5, 26, 12, 0)
    assert staleness(datetime(2026, 5, 26, 11, 50), fresh_within_hours=1, now=now) == "fresh"
    assert staleness(datetime(2026, 5, 26, 6, 0), fresh_within_hours=1, now=now) == "stale"
    assert staleness(None, fresh_within_hours=1, now=now) == "unknown"
