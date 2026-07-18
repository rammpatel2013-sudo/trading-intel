"""Tests for the pre-earnings straddle collector's pure expiry chooser.

Regression cover for the ``years_to_expiry`` call: it takes the expiration SERIES
plus a POSITIONAL ref date (dispatching on the column dtype), so the chooser must
pass the whole column, not per-scalar values.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from trading_intel.scheduler.jobs.pre_earnings_straddle import _choose_expiry

_AS_OF = date(2026, 7, 18)
_EPOCH = date(1970, 1, 1)


def _eday(d: date) -> int:
    return (d - _EPOCH).days


def test_choose_expiry_epoch_day_encoding():
    offs = [7, 21, 28, 49]
    rows = [
        {"expiration": _eday(_AS_OF + timedelta(days=o)), "strike": 100 + k}
        for o in offs
        for k in range(3)
    ]
    chosen = _choose_expiry(pd.DataFrame(rows), _AS_OF, earnings_dte=5, target_dte=30)
    assert chosen == _eday(_AS_OF + timedelta(days=28))  # closest to 30, brackets earnings


def test_choose_expiry_datetime_encoding():
    offs = [7, 21, 28, 49]
    rows = [{"expiration": pd.Timestamp(_AS_OF + timedelta(days=o)), "strike": 100} for o in offs]
    chosen = _choose_expiry(pd.DataFrame(rows), _AS_OF, earnings_dte=5, target_dte=30)
    assert chosen == pd.Timestamp(_AS_OF + timedelta(days=28))


def test_choose_expiry_must_bracket_earnings():
    # Earnings in 40d: must pick an expiry >= 40 even though 30d is closer to target.
    offs = [10, 30, 45]
    rows = [{"expiration": _eday(_AS_OF + timedelta(days=o)), "strike": 100} for o in offs]
    chosen = _choose_expiry(pd.DataFrame(rows), _AS_OF, earnings_dte=40, target_dte=30)
    assert chosen == _eday(_AS_OF + timedelta(days=45))


def test_choose_expiry_empty():
    assert _choose_expiry(pd.DataFrame({"expiration": []}), _AS_OF, 5, 30) is None
