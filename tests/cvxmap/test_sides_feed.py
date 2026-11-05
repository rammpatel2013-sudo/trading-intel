"""sides_feed (2026-11-05): per-trade sides — quote rule at the bid/ask, tick rule everywhere else."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import sides_feed as sf


def _p(px, loc=None, leg=False):
    return {"price": px, "quote_loc": loc, "spread_leg": leg}


def test_quote_rule_wins_at_bid_and_ask():
    out = sf.classify([_p(2.0, "at_ask"), _p(1.9, "at_ask"), _p(2.1, "at_bid")])
    assert out == [(1, "quote"), (1, "quote"), (-1, "quote")]


def test_tick_rule_for_mid_and_inside():
    out = sf.classify([_p(2.0, "at_bid"), _p(2.2, "mid"), _p(2.2, "inside"), _p(2.1, "mid")])
    # 2.2 > 2.0 up-tick -> buy; 2.2 zero tick keeps the up direction -> buy; 2.1 down-tick -> sell
    assert out == [(-1, "quote"), (1, "tick"), (1, "tick"), (-1, "tick")]


def test_first_print_at_mid_is_unsigned_and_legs_never_signed():
    out = sf.classify([_p(2.0, "mid"), _p(2.5, "at_ask", leg=True), _p(2.4, "mid")])
    assert out[0] == (0, "none")
    assert out[1] == (0, "leg")
    assert out[2] == (-1, "tick")      # the leg's 2.5 still sets the tick state: 2.4 is a down-tick


def test_minute_rounds_up():
    assert sf.minute_up(datetime(2026, 11, 5, 9, 30, 15)) == 571
    assert sf.minute_up(datetime(2026, 11, 5, 9, 31, 0)) == 571


def test_aggregate_buckets_and_vendor_agreement():
    t = datetime(2026, 11, 5, 10, 0, 5)
    rows = [
        {"id": 1, "symbol": "A", "ts": t, "strike": 7400.0, "cp": "P", "price": 5.0, "size": 10, "notional": 5000, "quote_loc": "at_ask", "spread_leg": False, "side": "buy"},
        {"id": 2, "symbol": "A", "ts": t, "strike": 7400.0, "cp": "P", "price": 4.8, "size": 4, "notional": 1920, "quote_loc": "mid", "spread_leg": False, "side": "buy"},
        {"id": 3, "symbol": "B", "ts": t, "strike": 7450.0, "cp": "C", "price": 3.0, "size": 2, "notional": 600, "quote_loc": "mid", "spread_leg": False, "side": None},
    ]
    agg, st = sf.aggregate(rows)
    assert agg[(7400.0, "P", 601)][:5] == [10, 0, 0, 4, 0]   # quote buy 10, tick sell 4 (down-tick)
    assert agg[(7450.0, "C", 601)][4] == 2                   # first print at mid -> unsigned
    assert st["quote_vendor_n"] == 1 and st["quote_vendor_agree"] == 1
    assert st["tick_vendor_n"] == 1 and st["tick_vendor_agree"] == 0
    js = sf.payload("2026-11-05", agg, st, 5000)
    assert js["rows"][0][:3] == [7400.0, "P", 601]


def test_tape_clock_and_shift_to_new_york():
    utc = [{"ts": datetime(2026, 11, 5, 19, 10, 0)}, {"ts": datetime(2026, 11, 5, 14, 31, 0)}]
    assert sf.tape_clock(utc) == "utc"
    et = [{"ts": datetime(2026, 11, 5, 9, 31, 0)}]
    assert sf.tape_clock(et) == "et"
    # tape only 13:40-14:15: decide by which clock the latest print sits near "now" (14:16 ET)
    mid = [{"ts": datetime(2026, 11, 5, 13, 40)}, {"ts": datetime(2026, 11, 5, 14, 15)}]
    assert sf.tape_clock(mid, datetime(2026, 11, 5, 14, 16)) == "et"
    rows = sf.to_et([{"ts": datetime(2026, 11, 5, 19, 10, 0)}], "utc")
    assert rows[0]["ts"] == datetime(2026, 11, 5, 14, 10, 0)   # Nov 5 = EST, UTC-5
