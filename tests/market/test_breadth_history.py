from datetime import date

from trading_intel.market import breadth_history as bh


class FakeFmp:
    def __init__(self, data):
        self.data = data

    def fmp(self, ep, params=None):
        return self.data[ep](params or {})


def test_historical_and_cumulate_splice():
    d1, d2, d3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    dated = {"A": {d1: 10, d2: 11, d3: 10}, "B": {d1: 5, d2: 6, d3: 7}, "C": {d1: 3, d2: 2}}
    daily = bh.historical_ad(dated)
    assert daily == [(d2, 2, 1), (d3, 1, 1)]
    cum = bh.cumulate_ad(daily, end_value=100.0)
    assert cum[-1][2] == 100.0 and cum[0][2] == 100.0 and cum[0][1] == 1


def test_batch_change_counts_and_holdings():
    q = lambda p: [{"symbol": s, "change": (1 if i % 2 else -1)} for i, s in enumerate(p["symbols"].split(","))]
    f = FakeFmp({"batch-quote": q,
                 "etf/holdings": lambda p: [{"asset": "AAA"}, {"asset": "BB.B"}, {"asset": "USD CASH"}]})
    assert bh.batch_change_counts(f, ["A", "B", "C", "D"], chunk=4) == (2, 2, 4)
    assert bh.etf_holding_symbols(f, "IJR") == ["AAA", "BB.B"]
