"""Tests for the FRED client (fake Fred SDK, no network)."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.clients.fred import (
    HY_OAS_SERIES,
    IG_OAS_SERIES,
    VIX_SERIES,
    FredClient,
)


class FakeFred:
    def __init__(self, data: dict):
        self._data = data

    def get_series(self, series_id):
        return self._data.get(series_id)


def _client(data) -> FredClient:
    return FredClient(settings=None, fred=FakeFred(data))


def test_vix_with_sd20():
    s = pd.Series([20.0 + (i % 5) for i in range(40)])
    last, sd20 = _client({VIX_SERIES: s}).vix_with_sd20()
    assert last == pytest.approx(float(s.iloc[-1]))
    assert sd20 == pytest.approx(float(s.tail(20).std(ddof=0)))


def test_vix_with_sd20_empty():
    assert _client({VIX_SERIES: pd.Series(dtype="float64")}).vix_with_sd20() == (None, None)


def test_credit_spreads():
    hy, ig = _client(
        {HY_OAS_SERIES: pd.Series([3.1, 3.2]), IG_OAS_SERIES: pd.Series([1.0, 1.1])}
    ).credit_spreads()
    assert hy == pytest.approx(3.2)
    assert ig == pytest.approx(1.1)


def test_latest_none_when_missing():
    assert _client({}).latest("NOPE") is None
