"""Tests for the intraday price-cone data layer (pure, no DB)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from trading_intel.dashboard.forward_cone_data import DRIVERS, intraday_cone


def _grid(n: int = 7, step_min: int = 10) -> list[datetime]:
    base = datetime(2026, 5, 27, 13, 0)
    return [base + timedelta(minutes=step_min * i) for i in range(n)]


def _frame(*, charm_sign: float = 1.0) -> pd.DataFrame:
    """Tiny near-the-money live_gex snapshot (spot 100).

    gxoi is set so the cumulative net (calls + / puts -) flips sign between strike
    100 and 105 (so the gex driver finds a flip) while the total net stays nonzero
    (so the vanna driver has a denominator).
    """
    ts = datetime(2026, 5, 27, 13, 0)
    gxoi = {(95, "C"): 100, (95, "P"): 500, (100, "C"): 300, (100, "P"): 400,
            (105, "C"): 700, (105, "P"): 100}
    rows = []
    for (strike, cp), gx in gxoi.items():
        rows.append({
            "ts": ts, "symbol": "X", "strike": float(strike), "cp": cp, "spot": 100.0,
            "iv": 0.20, "oi": 1000.0 if cp == "C" else 800.0, "gxoi": float(gx),
            "gamma": 0.5, "vanna": 10.0 if cp == "C" else 8.0,
            "charm": charm_sign * (2.0 if cp == "C" else 1.0),
        })
    return pd.DataFrame(rows)


def test_all_drivers_return_two_paths_starting_at_anchor() -> None:
    grid = _grid()
    frame = _frame()
    for driver in DRIVERS:
        cone = intraday_cone(driver, frame, anchor=100.0, grid=grid)
        assert list(cone.columns) == ["t", "up", "down"], driver
        assert len(cone) == len(grid), driver
        assert cone["up"].iloc[0] == pytest.approx(100.0), driver
        assert cone["down"].iloc[0] == pytest.approx(100.0), driver


def test_vol_band_widens_monotonically() -> None:
    cone = intraday_cone("vol", _frame(), anchor=100.0, grid=_grid())
    widths = (cone["up"] - cone["down"]).to_numpy()
    assert (widths[1:] >= widths[:-1] - 1e-9).all()  # non-decreasing
    assert widths[-1] > 0


def test_charm_paths_mirror_around_anchor() -> None:
    cone = intraday_cone("charm", _frame(charm_sign=1.0), anchor=100.0, grid=_grid())
    assert ((cone["up"] + cone["down"]) - 200.0).abs().max() < 1e-9  # mirror about anchor
    assert cone["up"].iloc[-1] > 100.0  # positive net charm -> up path drifts up by close


def test_gex_cone_uses_flip_distance() -> None:
    cone = intraday_cone("gex", _frame(), anchor=100.0, grid=_grid())
    assert not cone.empty
    assert cone["up"].iloc[-1] > cone["up"].iloc[0]  # band opens toward the close


def test_empty_on_bad_inputs() -> None:
    grid = _grid()
    frame = _frame()
    assert intraday_cone("nope", frame, 100.0, grid).empty
    assert intraday_cone("vol", frame, None, grid).empty
    assert intraday_cone("vol", frame, 100.0, grid[:1]).empty
    assert intraday_cone("vol", pd.DataFrame(), 100.0, grid).empty
