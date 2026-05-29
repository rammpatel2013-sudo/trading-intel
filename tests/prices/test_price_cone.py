"""Tests for the forward price cone."""

from __future__ import annotations

import numpy as np
import pytest

from trading_intel.prices.price_cone import forward_cone


def test_cone_shape_and_widening():
    cone = forward_cone(100.0, 0.20, horizon_days=21)
    assert list(cone.columns) == ["day", "median", "lo1", "hi1", "lo2", "hi2"]
    assert len(cone) == 21
    assert (cone["median"] == 100.0).all()
    # bands ordered lo2 < lo1 < median < hi1 < hi2 at every horizon
    assert (cone["lo2"] < cone["lo1"]).all()
    assert (cone["lo1"] < cone["median"]).all()
    assert (cone["median"] < cone["hi1"]).all()
    assert (cone["hi1"] < cone["hi2"]).all()
    # widens with time: day 21 band strictly wider than day 1
    w1 = cone.iloc[0]["hi1"] - cone.iloc[0]["lo1"]
    w21 = cone.iloc[-1]["hi1"] - cone.iloc[-1]["lo1"]
    assert w21 > w1


def test_cone_sqrt_time_and_value():
    cone = forward_cone(100.0, 0.20, horizon_days=21)
    # day 21: sigma_t = 0.20 * sqrt(21/252); hi1 = 100*exp(sigma_t)
    sigma_t = 0.20 * np.sqrt(21 / 252)
    assert cone.iloc[-1]["hi1"] == pytest.approx(100.0 * np.exp(sigma_t))
    assert cone.iloc[-1]["lo2"] == pytest.approx(100.0 * np.exp(-2.0 * sigma_t))


def test_cone_invalid_inputs_empty():
    assert forward_cone(None, 0.2).empty
    assert forward_cone(100.0, None).empty
    assert forward_cone(0.0, 0.2).empty
    assert forward_cone(100.0, -0.1).empty
    assert forward_cone(100.0, 0.2, horizon_days=0).empty
