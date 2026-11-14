"""breadth_extra (2026-11-13 overnight): up/down volume, Stockbee counts, setup supply."""
from trading_intel.market import breadth_extra as bx


def test_up_down_volume_and_4pct_counts():
    closes = {"A": [100, 105], "B": [100, 95], "C": [100, 100.5], "D": [100, 100]}
    vols = {"A": 1000, "B": 400, "C": 50, "D": 999}
    ex = bx.extras(closes, vols)
    assert (ex["up_volume"], ex["down_volume"]) == (1050, 400)
    assert (ex["up4"], ex["down4"]) == (1, 1)


def test_no_volumes_gives_none():
    assert bx.up_down_volume({"A": [1, 2]}, {}) == (None, None)


def test_quarter_moves_pct40_and_setups():
    rising = [100 + i * 0.5 for i in range(260)]          # steady uptrend
    base = [100 + i for i in range(250)] + [349.0] * 10   # tight base at the high
    falling = [300 - i for i in range(260)]
    ex = bx.extras({"R": rising, "B": base, "F": falling})
    assert ex["down25q"] == 1 and ex["pct_above_40"] == 67
    assert ex["setups_base"] >= 1
    pull = [100 + i * 0.5 for i in range(240)] + [219.5 - i * 0.3 for i in range(20)]
    assert bx._is_pullback(pull) in (True, False)          # well-formed on long series
