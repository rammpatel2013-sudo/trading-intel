"""Unit tests for the swing_report Stage-1 pure functions.

swing_report is a script (not an importable package module), so it's loaded by
path here.
"""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from trading_intel.errors import DataSourceError

_SR_PATH = Path(__file__).resolve().parents[1] / "scripts" / "swing_report.py"
_spec = importlib.util.spec_from_file_location("swing_report", _SR_PATH)
assert _spec is not None and _spec.loader is not None
swing_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(swing_report)


def test_realized_vol_constant_series_is_zero():
    assert swing_report.realized_vol(np.full(30, 100.0)) == pytest.approx(0.0)


def test_realized_vol_too_short_returns_none():
    assert swing_report.realized_vol(np.array([100.0, 101.0])) is None


def test_safe_returns_value_and_swallows_datasource_error():
    assert swing_report._safe(lambda: 42) == 42

    def boom():
        raise DataSourceError("502")

    assert swing_report._safe(boom) is None


def test_safe_propagates_non_datasource_errors():
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        swing_report._safe(boom)


def test_score_setup_bullish_cheap_vol():
    feat = {"px": 110.0, "sma50": 100.0, "rsi": 60.0, "dex": 1e6, "iv_rv": 0.9, "gex": 1e5}
    score, lean, structure = swing_report.score_setup(feat)
    assert lean == "bullish"
    assert score > 60
    assert "debit spread" in structure.lower()


def test_score_setup_neutral_no_edge():
    feat = {"px": None, "sma50": None, "rsi": 50.0, "dex": None, "iv_rv": None, "gex": None}
    _score, lean, _structure = swing_report.score_setup(feat)
    assert lean == "neutral"


def test_skew_25d_put_minus_call():
    exp = pd.Timestamp(date.today() + timedelta(days=40))
    chain = pd.DataFrame(
        [
            {"opt_kind": "call", "delta": 0.25, "iv": 0.20, "expiration": exp},
            {"opt_kind": "put", "delta": -0.25, "iv": 0.26, "expiration": exp},
        ]
    )
    assert swing_report.skew_25d(chain) == pytest.approx(0.06)
