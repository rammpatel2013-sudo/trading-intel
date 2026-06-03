"""Tests for the per-expiry surface panel (pure)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_intel.greeks.surface_panel import next_weekly_expiries, surface_panel

_GRID = [0.05, 0.075, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.475, 0.50]
_TODAY = date.today()  # anchor to today so fixture DTEs stay positive (no time-bomb)
_E1 = _TODAY + timedelta(days=23)  # a "weekly"


def _chain(iv_offset: float, expiries) -> pd.DataFrame:
    rows = []
    for exp in expiries:
        for n, d in enumerate(_GRID):
            rows.append((5000 - 100 * n, "P", 0.20 + iv_offset, -d, exp))
            rows.append((5000 + 100 * n, "C", 0.20 + iv_offset, d, exp))
    df = pd.DataFrame(rows, columns=["strike", "cp", "iv", "delta", "expiry"])
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def test_next_weekly_skips_near_term():
    exps = [_TODAY + timedelta(days=k) for k in (1, 9, 23, 43)]  # 1d skipped
    chain = _chain(0.0, exps)
    got = next_weekly_expiries(chain, n=3, min_dte=5, ref=_TODAY)
    assert got == [(_TODAY + timedelta(days=9)), (_TODAY + timedelta(days=23)),
                   (_TODAY + timedelta(days=43))]


def test_panel_fixed_delta_and_fixed_strike_changes():
    prev = _chain(0.00, [_E1])  # IV 20%
    curr = _chain(0.01, [_E1])  # IV 21% at every strike, deltas unchanged
    panels = surface_panel(curr, prev, [_E1])  # default percent delta grid
    assert len(panels) == 1
    p = panels[0]
    assert np.allclose(p.put_iv, 21.0, atol=0.1)  # today's IV in %
    # spot/deltas unchanged + uniform +1pt -> both change views ~ +1.0 vol pt
    assert np.nanmean(p.d_put_delta) == pytest.approx(1.0, abs=0.1)
    assert np.nanmean(p.d_put_strike) == pytest.approx(1.0, abs=0.1)
    assert np.nanmean(p.d_call_delta) == pytest.approx(1.0, abs=0.1)
