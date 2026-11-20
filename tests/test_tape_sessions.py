"""Tape session windows (2026-11-19, A1b)."""

from datetime import date, datetime

import pytest

from trading_intel.tape_sessions import GTH_POLL_S, poll_interval, session_trade_date, tape_session


@pytest.mark.parametrize(
    ("ts", "sess", "day"),
    [
        (datetime(2026, 11, 19, 20, 40), "gth", date(2026, 11, 20)),  # Thu evening -> Fri session
        (datetime(2026, 11, 20, 3, 0), "gth", date(2026, 11, 20)),
        (datetime(2026, 11, 20, 8, 0), "pre", date(2026, 11, 20)),
        (datetime(2026, 11, 20, 10, 0), "rth", date(2026, 11, 20)),
        (datetime(2026, 11, 20, 16, 5), "post", date(2026, 11, 20)),
        (datetime(2026, 11, 20, 16, 30), "curb", date(2026, 11, 20)),
        (datetime(2026, 11, 20, 18, 0), None, date(2026, 11, 20)),  # dead zone
        (datetime(2026, 11, 20, 21, 0), None, date(2026, 11, 23)),  # Friday night: no GTH
        (datetime(2026, 11, 22, 21, 0), "gth", date(2026, 11, 23)),  # Sunday night -> Monday
        (datetime(2026, 11, 25, 21, 0), None, date(2026, 11, 27)),  # eve of Thanksgiving
        (datetime(2026, 11, 26, 10, 0), None, date(2026, 11, 27)),  # Thanksgiving
        (datetime(2026, 11, 26, 21, 0), "gth", date(2026, 11, 27)),
    ],
)
def test_sessions_and_trade_dates(ts, sess, day):
    assert tape_session(ts) == sess
    assert session_trade_date(ts) == day


def test_overnight_polls_slower():
    assert poll_interval(datetime(2026, 11, 20, 2, 0), 30) == GTH_POLL_S
    assert poll_interval(datetime(2026, 11, 20, 11, 0), 30) == 30
