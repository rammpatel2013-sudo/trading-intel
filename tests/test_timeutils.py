"""Tests for the Eastern-Time stamping helper."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trading_intel.timeutils import eastern_now, same_day_expired, same_day_years


def test_eastern_now_is_naive():
    assert eastern_now().tzinfo is None


def test_eastern_now_offset_from_utc():
    # ET is UTC-4 (EDT) or UTC-5 (EST); the naive ET wall clock should sit that
    # far behind UTC. Allow generous slack for the two calls + DST edges.
    et = eastern_now()
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = (utc - et).total_seconds() / 3600.0
    assert 3.5 <= diff_hours <= 5.5


def test_same_day_expired_only_after_the_close_of_that_day():
    d = date(2026, 10, 22)
    assert not same_day_expired(d, datetime(2026, 10, 22, 8, 40))
    assert not same_day_expired(d, datetime(2026, 10, 22, 16, 10))
    assert same_day_expired(d, datetime(2026, 10, 22, 16, 15))
    assert not same_day_expired(d, datetime(2026, 10, 23, 17, 0))  # an older book is as-of


def test_same_day_years_live_and_as_of():
    d, h = date(2026, 10, 22), 1.0 / (365 * 24)
    assert abs(same_day_years(d, datetime(2026, 10, 22, 8, 40)) - 6.5 * h) < 1e-9   # pre-open: full session
    assert abs(same_day_years(d, datetime(2026, 10, 22, 13, 0)) - 3.0 * h) < 1e-9   # live: to 16:00
    assert abs(same_day_years(d, datetime(2026, 10, 22, 15, 45)) - 1.0 * h) < 1e-9  # one-hour floor
    assert abs(same_day_years(d, datetime(2026, 10, 30, 9, 0)) - 6.5 * h) < 1e-9    # as-of book
