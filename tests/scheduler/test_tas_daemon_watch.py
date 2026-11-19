"""Tape daemon recovery rules (2026-11-19 fix: the 11/13 tape stopped at 11:03 and never came back)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from trading_intel.scheduler.jobs import tas_capture_job as job
from trading_intel.scheduler.jobs.tas_capture_daemon import _MAX_BACKOFF_S, _Watch, _auth_error

T = datetime(2026, 11, 20, 10, 0)


def test_backoff_capped_at_60s() -> None:
    w = _Watch(T)
    waits = []
    for _ in range(8):
        w.fails += 1
        waits.append(w.backoff(30))
    assert waits[:2] == [30, 60] and max(waits) == _MAX_BACKOFF_S == 60


def test_relogin_after_three_fails_or_auth_error() -> None:
    w = _Watch(T)
    w.fails = 1
    assert not w.need_relogin("timeout") and w.need_relogin("HTTP 401 Unauthorized")
    w.fails = 3
    assert w.need_relogin(None)
    assert _auth_error("403 Forbidden") and not _auth_error("ReadTimeout")


def test_yield_to_stale_cockpit_gives_up_after_10_min() -> None:
    w = _Watch(T)
    assert w.yield_now(T, True)
    assert w.yield_now(T + timedelta(minutes=9), True)
    assert not w.yield_now(T + timedelta(minutes=10, seconds=1), True)
    assert not w.yield_now(T + timedelta(minutes=11), False) and w.stale_since is None


def test_alarm_once_down_then_once_up() -> None:
    w = _Watch(T)
    assert w.check_alarm(T + timedelta(minutes=9)) is None
    assert w.check_alarm(T + timedelta(minutes=10)) == "down"
    assert w.check_alarm(T + timedelta(minutes=20)) is None          # only once
    w.last_ok = T + timedelta(minutes=21)
    assert w.check_alarm(T + timedelta(minutes=21)) == "up"
    assert w.check_alarm(T + timedelta(minutes=22)) is None


def test_no_alarm_outside_market_hours() -> None:
    w = _Watch(datetime(2026, 11, 20, 9, 0))
    assert w.check_alarm(datetime(2026, 11, 20, 9, 39)) is None       # before 09:40
    w2 = _Watch(datetime(2026, 11, 20, 15, 40))
    assert w2.check_alarm(datetime(2026, 11, 20, 16, 5)) is None      # after the close


def test_print_time_stored_as_naive_eastern() -> None:
    row = pd.Series({
        "symbol": ".SPXW261120C7600", "time": pd.Timestamp("2026-11-20 15:30:05", tz="UTC"),
        "price": 10.0, "size": 500, "side": "buy", "delta": 0.4,
    })
    rec = job._row_record(row, captured_at=T, trade_date=T.date(), min_premium=1.0,
                          exclude=frozenset(), index_roots=frozenset({"SPX", "SPXW"}), index_base_premium=1.0)
    assert rec is not None and rec["ts"] == datetime(2026, 11, 20, 10, 30, 5) and rec["ts"].tzinfo is None
