"""Tests for the Eastern-Time stamping helper."""

from __future__ import annotations

from datetime import date, datetime, timezone

from trading_intel.timeutils import eastern_now, same_day_expired, same_day_years, same_front_basis


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


def test_live_expiry_floor_drops_the_settled_same_day_expiry_for_any_later_read():
    from trading_intel.timeutils import live_expiry_floor, same_day_settled

    fri = date(2026, 10, 23)
    assert live_expiry_floor(fri, datetime(2026, 10, 23, 9, 0)) == fri            # live session
    assert live_expiry_floor(fri, datetime(2026, 10, 23, 16, 15)) == date(2026, 10, 24)
    assert live_expiry_floor(fri, datetime(2026, 10, 25, 17, 0)) == date(2026, 10, 24)  # Sunday read
    assert not same_day_settled(fri, datetime(2026, 10, 23, 16, 14))
    assert same_day_settled(fri, datetime(2026, 10, 26, 8, 0))


def test_same_day_years_live_and_as_of():
    d, h = date(2026, 10, 22), 1.0 / (365 * 24)
    assert abs(same_day_years(d, datetime(2026, 10, 22, 8, 40)) - 6.5 * h) < 1e-9   # pre-open: full session
    assert abs(same_day_years(d, datetime(2026, 10, 22, 13, 0)) - 3.0 * h) < 1e-9   # live: to 16:00
    assert abs(same_day_years(d, datetime(2026, 10, 22, 15, 45)) - 1.0 * h) < 1e-9  # one-hour floor
    assert abs(same_day_years(d, datetime(2026, 10, 30, 9, 0)) - 6.5 * h) < 1e-9    # as-of book


def test_same_front_basis_splits_at_the_change_date():
    assert same_front_basis(date(2026, 10, 20), date(2026, 10, 21))
    assert not same_front_basis(date(2026, 10, 21), date(2026, 10, 22))
    assert same_front_basis(datetime(2026, 10, 22, 8, 35), "2026-10-29")
