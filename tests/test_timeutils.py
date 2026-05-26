"""Tests for the Eastern-Time stamping helper."""

from __future__ import annotations

from datetime import datetime, timezone

from trading_intel.timeutils import eastern_now


def test_eastern_now_is_naive():
    assert eastern_now().tzinfo is None


def test_eastern_now_offset_from_utc():
    # ET is UTC-4 (EDT) or UTC-5 (EST); the naive ET wall clock should sit that
    # far behind UTC. Allow generous slack for the two calls + DST edges.
    et = eastern_now()
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    diff_hours = (utc - et).total_seconds() / 3600.0
    assert 3.5 <= diff_hours <= 5.5
