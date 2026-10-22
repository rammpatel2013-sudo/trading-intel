from datetime import date

from trading_intel.market import wall_conviction as wc


def _read(**kw):
    base = dict(spot=100.0, wall_history=[105, 105, 105], fsv_series=[0.40, 0.40, 0.40, 0.395],
                earnings_in_days=None, oi_rank_value=0.9, wall_volume=5000)
    base.update(kw)
    return wc.read_wall("call", **base)


def test_hold_when_stable_at_level_and_vol_calm():
    r = _read(spot=104.5)
    assert r.verdict == "HOLD" and r.stable and r.proximity == "at"


def test_break_when_vol_bid_at_wall():
    r = _read(spot=104.5, fsv_series=[0.40, 0.41, 0.42, 0.43])
    assert r.verdict == "BREAK" and r.fsv_state == "bid"


def test_no_read_when_far_or_migrating():
    assert _read(spot=95.0).verdict == "NO READ"
    assert _read(spot=104.5, wall_history=[100, 102.5, 105]).verdict == "NO READ"


def test_wait_into_earnings():
    assert _read(spot=104.5, earnings_in_days=4).verdict == "WAIT"


def test_put_wall_mirror_and_through():
    r = wc.read_wall("put", spot=95.5, wall_history=[95, 95, 95], fsv_series=[0.5, 0.5, 0.5, 0.5])
    assert r.proximity == "at" and r.verdict == "HOLD"
    assert wc.proximity("put", 90.0, 95.0)[0] == "through"


def test_thin_and_helpers():
    assert wc.is_thin(0.2, 5000) is True
    assert wc.is_thin(0.9, 10) is True
    assert wc.is_thin(0.9, 5000) is False
    assert wc.oi_rank(50, [10, 50, 100, 200]) == 0.5
    assert wc.earnings_days(date(2026, 10, 2), [date(2026, 9, 1), date(2026, 10, 9)]) == 7
    r = _read(spot=104.5, oi_rank_value=0.1)
    assert r.verdict == "HOLD" and r.thin and any("thin" in x for x in r.reasons)
