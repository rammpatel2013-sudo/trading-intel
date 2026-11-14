"""Rule sd-v1 on synthetic series (2026-11-13)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading_intel.strategies import skew_divergence as sd


def _days(n):
    d, out = date(2025, 6, 2), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _series(n=300, drop_last=12, rr_jump=-6.0, seed=1):
    rng = np.random.default_rng(seed)
    days = _days(n)
    px = 100 + np.cumsum(rng.normal(0, 0.3, n))
    px[-drop_last:] = px[-drop_last - 1] * np.linspace(0.985, 0.85, drop_last)  # steady slide to lows
    rr = 3 + rng.normal(0, 0.4, n)
    rr[-5:] = rr[-6] + np.linspace(rr_jump / 5, rr_jump, 5)  # skew collapses toward calls
    return pd.Series(px, index=days), pd.Series(rr, index=days)


def test_fires_on_drop_new_low_and_skew_to_calls():
    px, rr = _series()
    df = sd.evaluate(px, rr)
    assert bool(df["fire"].iloc[-1]) and bool(df["episode"].iloc[-5:].any())
    st = sd.status(df)
    assert st["verdict"].startswith("READY") and st["conditions"]["at a 10-day low"] == "yes"
    assert st["signal"]["sessions_left_in_hold"] >= sd.HOLD_SESSIONS - 5


def test_no_fire_when_skew_does_not_move():
    px, rr = _series(rr_jump=0.0)
    df = sd.evaluate(px, rr)
    assert not bool(df["fire"].iloc[-1])
    assert sd.status(df)["verdict"].startswith(("NOT READY", "CLOSE"))


def test_cooldown_keeps_one_episode():
    px, rr = _series()
    df = sd.evaluate(px, rr)
    fires = np.where(df["fire"].to_numpy())[0]
    eps = np.where(df["episode"].to_numpy())[0]
    assert len(eps) <= len(fires)
    assert all(b - a > sd.COOLDOWN for a, b in zip(eps, eps[1:]))


def test_cant_score_without_skew_history():
    px, rr = _series(n=100)
    st = sd.status(sd.evaluate(px, rr))
    assert st["verdict"].startswith("CAN'T SCORE")
    assert "no skew data" in st["conditions"]["skew moving to calls (5-day change in bottom 10% of the year)"]


def test_trailing_pct_has_no_lookahead():
    x = np.arange(200, dtype=float)
    p = sd.trailing_pct(x, window=252, min_obs=120)
    assert np.isnan(p[:119]).all() and p[150] > 0.99  # each value is the highest so far
