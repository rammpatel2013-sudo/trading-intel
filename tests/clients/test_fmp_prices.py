"""FMP price source + FMP-first wrapper (2026-11-13 overnight, Yahoo exit step 1)."""
import pandas as pd

from trading_intel.clients.fmp_prices import FmpFirstPriceSource, FmpPriceSource


class _CV:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def fmp(self, ep, params):
        self.calls.append((ep, params))
        return self.rows


class _Yahoo:
    def daily_history(self, symbol, *, period="5y"):
        return pd.DataFrame({"date": [pd.Timestamp("2026-11-12")], "open": [1], "high": [1], "low": [1],
                             "close": [1.0], "volume": [1]})


def test_fmp_maps_spx_and_sorts():
    cv = _CV([{"date": "2026-11-13", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 9},
              {"date": "2026-11-12", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 8}])
    df = FmpPriceSource(cv).daily_history("SPX", period="5d")
    assert cv.calls[0][1]["symbol"] == "^GSPC"
    assert list(df["close"]) == [1.5, 2.5] and list(df.columns)[:2] == ["date", "open"]


def test_fallback_only_when_fmp_empty_and_allowed():
    empty = FmpPriceSource(_CV([]))
    assert len(FmpFirstPriceSource(empty, _Yahoo(), allow_yahoo=True).daily_history("XYZ")) == 1
    assert FmpFirstPriceSource(empty, _Yahoo(), allow_yahoo=False).daily_history("XYZ").empty
    full = FmpPriceSource(_CV([{"date": "2026-11-13", "close": 5, "open": 5, "high": 5, "low": 5, "volume": 1}]))
    assert float(FmpFirstPriceSource(full, _Yahoo()).daily_history("SPY")["close"].iloc[0]) == 5.0
