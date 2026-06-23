"""Tests for day-over-day surface changes — pure, no DB/network."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface_changes import (
    atm_term_changes,
    fixed_strike_changes,
    format_atm_changes_markdown,
    format_fixed_strike_changes_markdown,
)


def _delta_chain(expiries: list[str], atm: float) -> pd.DataFrame:
    """Delta-rich chain (7 strikes per wing per expiry) at a given ATM IV level."""
    # Anchor the snapshot 10d before the nearest expiry so DTE stays positive
    # regardless of when the test runs.
    ts = min(pd.Timestamp(e) for e in expiries) - pd.Timedelta(days=10)
    rows: list[dict] = []
    for exp in expiries:
        for d in (0.05, 0.1, 0.2, 0.3, 0.4, 0.45, 0.5):
            rows.append(
                {"expiration": pd.Timestamp(exp), "opt_kind": "call", "delta": d,
                 "strike": 7400 + round(d * 1000), "iv": atm + (0.5 - d) * 0.1, "ts": ts}
            )
            rows.append(
                {"expiration": pd.Timestamp(exp), "opt_kind": "put", "delta": -d,
                 "strike": 7400 - round(d * 1000), "iv": atm + (0.5 - d) * 0.12, "ts": ts}
            )
    return pd.DataFrame(rows)


def test_fixed_strike_changes_diff_and_overlap():
    prev = pd.DataFrame(
        [
            {"expiration": "2026-06-18", "strike": 7400, "opt_kind": "call", "iv": 0.15},
            {"expiration": "2026-06-18", "strike": 7100, "opt_kind": "put", "iv": 0.20},
            {"expiration": "2026-06-18", "strike": 9999, "opt_kind": "put", "iv": 0.30},
        ]
    )
    curr = pd.DataFrame(
        [
            {"expiration": "2026-06-18", "strike": 7400, "opt_kind": "call", "iv": 0.17},
            {"expiration": "2026-06-18", "strike": 7100, "opt_kind": "put", "iv": 0.18},
            {"expiration": "2026-06-18", "strike": 8888, "opt_kind": "call", "iv": 0.25},
        ]
    )
    ch = fixed_strike_changes(prev, curr)
    assert len(ch) == 2  # only overlapping strikes (9999/8888 dropped)
    assert set(ch["strike"]) == {7400.0, 7100.0}
    call = ch[(ch["strike"] == 7400.0) & (ch["opt_kind"] == "C")].iloc[0]
    put = ch[(ch["strike"] == 7100.0) & (ch["opt_kind"] == "P")].iloc[0]
    assert call["d_iv_pts"] == pytest.approx(2.0)
    assert put["d_iv_pts"] == pytest.approx(-2.0)


def test_fixed_strike_changes_errors():
    good = pd.DataFrame([{"expiration": "2026-06-18", "strike": 1, "opt_kind": "call", "iv": 0.1}])
    with pytest.raises(ComputationError):
        fixed_strike_changes(pd.DataFrame(), good)
    # no overlap -> ComputationError
    other = pd.DataFrame([{"expiration": "2026-06-18", "strike": 2, "opt_kind": "call", "iv": 0.2}])
    with pytest.raises(ComputationError):
        fixed_strike_changes(good, other)


def test_atm_term_changes_per_expiry():
    prev = _delta_chain(["2026-06-18", "2026-07-17"], 0.15)
    curr = _delta_chain(["2026-06-18", "2026-07-17"], 0.18)
    atm = atm_term_changes(prev, curr)
    assert len(atm) == 2
    # ATM lifted +3 vol pts on both expiries
    assert atm["d_atm_pts"].iloc[0] == pytest.approx(3.0, abs=0.2)
    assert atm["d_atm_pts"].iloc[1] == pytest.approx(3.0, abs=0.2)


def test_format_changes_markdown():
    prev = _delta_chain(["2026-06-18"], 0.15)
    curr = _delta_chain(["2026-06-18"], 0.18)
    fs = format_fixed_strike_changes_markdown(fixed_strike_changes(prev, curr))
    assert fs.startswith("## Fixed-strike vol changes")
    assert "vol pts" in fs
    atm = format_atm_changes_markdown(atm_term_changes(prev, curr))
    assert atm.startswith("## ATM vol changes")
    assert "2026-06-18" in atm


def test_format_changes_markdown_empty():
    assert "No overlapping strikes" in format_fixed_strike_changes_markdown(pd.DataFrame())
    assert "No common expiries" in format_atm_changes_markdown(pd.DataFrame())
