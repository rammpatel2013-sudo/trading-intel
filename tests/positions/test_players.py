"""Named repeat traders (2026-11-22): the shapes VolSignals disclosed map to their players; unknown big repeats get
a stable auto- slug for Mithil to name."""

from datetime import date

from trading_intel.positions import players as P


def _ev(**k):
    base = {
        "cp": "P",
        "structure": "single",
        "side": 1,
        "dte": 79,
        "otm": -5.8,
        "width": None,
        "lots": 9716,
        "premium": 6e7,
        "clips": 30,
        "expiry": date(2026, 12, 31),
    }
    base.update(k)
    return base


def test_year_end_put_whale():
    assert P.classify(_ev()) == "year-end-put-whale"
    assert (
        P.classify(_ev(expiry=date(2027, 1, 15))) != "year-end-put-whale"
    )  # not a quarter-end line


def test_weekly_put_spread_seller_and_roller():
    ev = _ev(
        structure="vertical",
        side=-1,
        dte=45,
        otm=-18.0,
        width=100,
        lots=60000,
        expiry=date(2027, 1, 8),
    )
    assert P.classify(ev) == "weekly-putspread-seller"
    roll = _ev(
        structure="calendar",
        side=-1,
        dte=54,
        otm=-9.5,
        width=25,
        lots=7900,
        expiry=date(2027, 1, 15),
    )
    assert P.classify(roll) == "putspread-roller-25w"


def test_0dte_whale_by_size_or_premium():
    assert P.classify(_ev(dte=0, lots=2000, expiry=date(2026, 11, 20))) == "odte-whale"
    assert P.classify(_ev(dte=0, lots=300, premium=2e6, expiry=date(2026, 11, 20))) == "odte-whale"


def test_unknown_big_repeat_gets_stable_auto_slug():
    a = _ev(cp="C", side=-1, dte=120, otm=8.0, lots=6000, expiry=date(2027, 3, 19))
    b = dict(a, lots=6200, otm=8.4)
    assert P.classify(a).startswith("auto-") and P.classify(a) == P.classify(b)
    assert P.classify(dict(a, lots=200, premium=1e5)) is None  # small: ignored
    assert P.player_status(P.classify(a), 1, False) == "single"
    assert P.player_status(P.classify(a), 2, False) == "single"
    assert P.player_status(P.classify(a), 3, False) == "unnamed"
    assert P.classify(dict(a, dte=7, lots=6000, premium=1e6)) is None  # routine short-dated size
    assert P.player_status("year-end-put-whale", 0, False) == "named"
