"""Desk scorecard rules (2026-11-14): next-open entry, N-session close exit, vs SPY."""

from datetime import date, timedelta

from trading_intel.desk import score


def _bars(start: date, closes: list[float]):
    return [(start + timedelta(days=i), c, c, c, c, 1) for i, c in enumerate(closes)]


def test_trade_uses_next_open_and_nth_close():
    b = _bars(date(2026, 1, 1), [100, 101, 102, 103, 104, 105, 106])
    assert score.trade(b, date(2026, 1, 1), 5) == 100 * (105 / 101 - 1)
    assert score.trade(b, date(2026, 1, 5), 5) is None  # not enough sessions yet


def test_scorecard_excess_and_open():
    spy = _bars(date(2026, 1, 1), [100] * 30)
    up = _bars(date(2026, 1, 1), [100 + i for i in range(30)])
    flat = _bars(date(2026, 1, 1), [100] * 30)
    picks = [
        {"date": date(2026, 1, 1), "source": "ttwd", "symbol": "UP", "role": "top"},
        {"date": date(2026, 1, 1), "source": "ttwd", "symbol": "UP", "role": "screen"},
        {"date": date(2026, 1, 1), "source": "ttwd", "symbol": "FL", "role": "screen"},
        {"date": date(2026, 1, 20), "source": "ours", "symbol": "UP", "role": "two_leg"},
    ]
    sc = score.scorecard(picks, {"UP": up, "FL": flat}, spy)
    top5 = sc["stats"]["ttwd"]["top"][5]
    assert top5["n"] == 1 and top5["excess"] > 0 and top5["beat"] == 100.0
    rest = sc["stats"]["ttwd"]["rest"][5]  # screen minus top = FL only
    assert rest["n"] == 1 and abs(rest["excess"]) < 1e-9
    assert any(o["source"] == "ours" and o["symbol"] == "UP" for o in sc["open"])
