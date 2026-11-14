"""FMP price source + the index fetchers after the Yahoo exit (step 1 2026-11-13, step 2 2026-11-14)."""
import pandas as pd

from trading_intel.clients import fmp_prices, prices
from trading_intel.clients.fmp_prices import FmpPriceSource


class _CV:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def fmp(self, ep, params):
        self.calls.append((ep, params))
        return self.rows


def _bars(*closes):
    return pd.DataFrame({"date": pd.date_range("2026-11-10", periods=len(closes)), "open": closes,
                         "high": closes, "low": closes, "close": closes, "volume": [1] * len(closes)})


def test_fmp_maps_spx_and_sorts():
    cv = _CV([{"date": "2026-11-13", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 9},
              {"date": "2026-11-12", "open": 1, "high": 2, "low": 1, "close": 1.5, "volume": 8}])
    df = FmpPriceSource(cv).daily_history("SPX", period="5d")
    assert cv.calls[0][1]["symbol"] == "^GSPC"
    assert list(df["close"]) == [1.5, 2.5] and list(df.columns)[:2] == ["date", "open"]


def test_fmp_period_max_reaches_far_back():
    cv = _CV([])
    assert FmpPriceSource(cv).daily_history("SPLV", period="max").empty
    assert cv.calls[0][1]["from"] < "1930-01-01"


def test_fmp_price_source_wires_cvforge(monkeypatch):
    seen = {}

    class _Client:
        def __init__(self, settings, *, timeout=30.0):
            seen["settings"], seen["timeout"] = settings, timeout

    import trading_intel.clients.cvforge as cvf

    monkeypatch.setattr(cvf, "CVForgeClient", _Client)
    src = fmp_prices.fmp_price_source(settings="S", timeout=60.0)
    assert isinstance(src, FmpPriceSource) and seen == {"settings": "S", "timeout": 60.0}


def test_fmp_index_close_latest_value_and_guards(monkeypatch):
    class _Src:
        def __init__(self, df):
            self.df = df

        def daily_history(self, symbol, *, period="5y"):
            return self.df

    monkeypatch.setattr(fmp_prices, "fmp_price_source", lambda *a, **k: _Src(_bars(11.0, 11.8)))
    assert prices.fetch_fmp_index_close("^COR1M") == 11.8
    monkeypatch.setattr(fmp_prices, "fmp_price_source", lambda *a, **k: _Src(_bars(5000.0)))
    assert prices.fetch_fmp_index_close("^COR1M") is None  # range guard (#82)
    monkeypatch.setattr(fmp_prices, "fmp_price_source", lambda *a, **k: _Src(_bars()))
    assert prices.fetch_fmp_index_close("^COR1M") is None  # empty


def test_cboe_index_falls_back_to_fmp_not_yahoo(monkeypatch):
    import trading_intel.clients.cboe as cboe

    monkeypatch.setattr(cboe.CboeClient, "quote", lambda self, sym: None)
    asked = []
    monkeypatch.setattr(prices, "fetch_fmp_index_close", lambda sym: asked.append(sym) or 12.5)
    assert prices.fetch_cor1m() == 12.5 and asked == ["^COR1M"]
    monkeypatch.setattr(cboe.CboeClient, "quote", lambda self, sym: 13.0)
    assert prices.fetch_cor3m() == 13.0 and asked == ["^COR1M"]  # Cboe hit: FMP not asked


def test_yfinance_price_source_is_gone():
    assert not hasattr(prices, "YFinancePriceSource")
    assert not hasattr(prices, "fetch_yf_index_close")
    assert not hasattr(fmp_prices, "FmpFirstPriceSource")
