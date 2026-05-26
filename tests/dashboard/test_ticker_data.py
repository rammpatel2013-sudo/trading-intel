"""Tests for the per-ticker dashboard data-prep helpers.

Pure indicators/aggregations are tested directly; DB readers run against
in-memory SQLite with only the table under test created.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.ticker_data import (
    available_chain_dates,
    bollinger_bands,
    dex_by_strike,
    gex_by_strike,
    latest_snapshot,
    load_chain_at,
    load_latest_chain,
    load_quotes,
    load_snapshot_history,
    near_spot,
    normal_fit_by_strike,
    rolling_avg_by_strike,
    rsi,
    sma,
    snapshot_spot_flip,
    volume_by_strike_side,
)
from trading_intel.memory.models import GreeksChain, GreeksSnapshot, QuoteDaily

# ── indicators ─────────────────────────────────────────────────────────


def test_sma_matches_manual_mean():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_bollinger_bands_midline_and_width():
    s = pd.Series(np.arange(1.0, 11.0))
    bb = bollinger_bands(s, window=5, n_std=2.0)
    assert bb.mid.iloc[4] == pytest.approx(3.0)  # mean(1..5)
    std = float(pd.Series([1, 2, 3, 4, 5]).std(ddof=0))
    assert bb.upper.iloc[4] == pytest.approx(3.0 + 2 * std)
    assert bb.lower.iloc[4] == pytest.approx(3.0 - 2 * std)


def test_rsi_bounds_and_extremes():
    rising = pd.Series(np.arange(1.0, 30.0))
    falling = pd.Series(np.arange(30.0, 1.0, -1.0))
    r_up = rsi(rising, window=14)
    r_down = rsi(falling, window=14)
    assert r_up.dropna().between(0, 100).all()
    assert r_up.dropna().iloc[-1] == pytest.approx(100.0)
    assert r_down.dropna().iloc[-1] == pytest.approx(0.0)
    # Warm-up: not enough data to fill the first window.
    assert r_up.iloc[:14].isna().all()


# ── per-strike aggregations ──────────────────────────────────────────────


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"strike": 100.0, "opt_kind": "call", "gxoi": 10.0, "dxoi": 4.0},
            {"strike": 100.0, "opt_kind": "put", "gxoi": 3.0, "dxoi": -2.0},
            {"strike": 105.0, "opt_kind": "call", "gxoi": 6.0, "dxoi": 1.0},
        ]
    )


def test_gex_by_strike_signs_calls_plus_puts_minus():
    out = gex_by_strike(_chain())
    row100 = out.loc[out["strike"] == 100.0, "gex"].iloc[0]
    row105 = out.loc[out["strike"] == 105.0, "gex"].iloc[0]
    assert row100 == pytest.approx(10.0 - 3.0)
    assert row105 == pytest.approx(6.0)
    assert list(out["strike"]) == [100.0, 105.0]  # ascending


def test_dex_by_strike_sums_signed_dxoi():
    out = dex_by_strike(_chain())
    row100 = out.loc[out["strike"] == 100.0, "dex"].iloc[0]
    assert row100 == pytest.approx(4.0 - 2.0)


def test_by_strike_empty_in_empty_out():
    assert gex_by_strike(pd.DataFrame()).empty
    assert dex_by_strike(pd.DataFrame()).empty


def test_near_spot_filters_and_passthrough():
    df = pd.DataFrame({"strike": [90.0, 95.0, 100.0, 105.0, 110.0], "gex": [1, 2, 3, 4, 5]})
    out = near_spot(df, 100.0, 0.06)  # window 94..106
    assert list(out["strike"]) == [95.0, 100.0, 105.0]
    # pct=None -> full chain unchanged; empty frame -> empty.
    assert list(near_spot(df, 100.0, None)["strike"]) == [90.0, 95.0, 100.0, 105.0, 110.0]
    assert near_spot(pd.DataFrame(), 100.0, 0.05).empty


def test_snapshot_spot_flip_selects_by_ts():
    snaps = pd.DataFrame(
        {
            "ts": [datetime(2026, 5, 25, 6, 45), datetime(2026, 5, 26, 6, 45)],
            "spot": [7400.0, 7500.0],
            "gex_flip": [7350.0, 7450.0],
        }
    )
    assert snapshot_spot_flip(snaps) == (7500.0, 7450.0)  # latest when ts None
    # nearest to the older snapshot
    assert snapshot_spot_flip(snaps, datetime(2026, 5, 25, 6, 45)) == (7400.0, 7350.0)
    assert snapshot_spot_flip(pd.DataFrame()) == (None, None)


def test_volume_by_strike_side_splits_calls_puts():
    frame = pd.DataFrame(
        {"strike": [100, 100, 105, 105], "cp": ["C", "P", "C", "P"], "volume": [10, 4, 7, 9]}
    )
    out = volume_by_strike_side(frame)
    assert list(out["strike"]) == [100, 105]
    assert list(out["call"]) == [10.0, 7.0]
    assert list(out["put"]) == [4.0, 9.0]
    # only calls -> put column zero-filled; empty -> empty.
    only_c = volume_by_strike_side(pd.DataFrame({"strike": [100], "cp": ["C"], "volume": [5]}))
    assert list(only_c["call"]) == [5.0] and list(only_c["put"]) == [0.0]
    assert volume_by_strike_side(pd.DataFrame()).empty


def test_rolling_avg_by_strike_smooths():
    df = pd.DataFrame({"strike": [1, 2, 3], "gex": [0.0, 3.0, 0.0]})
    roll = rolling_avg_by_strike(df, "gex", window=3)
    assert roll.iloc[1] == pytest.approx(1.0)  # mean(0,3,0)


def test_normal_fit_centre_and_degenerate():
    df = pd.DataFrame(
        {"strike": [90.0, 100.0, 110.0], "gex": [2.0, 8.0, 2.0]}
    )
    fit = normal_fit_by_strike(df, "gex", n_points=50)
    assert fit is not None
    assert fit.mean == pytest.approx(100.0)
    assert fit.fit.max() == pytest.approx(8.0)  # scaled to largest bar
    # A single strike cannot define a spread.
    assert normal_fit_by_strike(pd.DataFrame({"strike": [100.0], "gex": [5.0]}), "gex") is None


# ── DB readers (SQLite) ───────────────────────────────────────────────────


@pytest.fixture
def chain_session() -> Session:
    engine = create_engine("sqlite://")
    GreeksChain.__table__.create(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def snap_session() -> Session:
    engine = create_engine("sqlite://")
    GreeksSnapshot.__table__.create(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def quote_session() -> Session:
    engine = create_engine("sqlite://")
    QuoteDaily.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_load_latest_chain_returns_only_newest_snapshot(chain_session: Session):
    old = datetime(2026, 5, 21, 20, 0, tzinfo=UTC)
    new = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
    for ts, gx in ((old, 1.0), (new, 9.0)):
        chain_session.add(
            GreeksChain(symbol="SPY", ts=ts, expiry=ts.date(), strike=500.0,
                        cp="C", gxoi=gx, dxoi=gx, source="convex")
        )
    chain_session.commit()
    ts, df = load_latest_chain(chain_session, "SPY")
    # SQLite's DateTime returns naive datetimes; compare on the wall-clock value.
    assert ts.replace(tzinfo=None) == new.replace(tzinfo=None)
    assert len(df) == 1
    assert df["gxoi"].iloc[0] == pytest.approx(9.0)
    assert df["opt_kind"].iloc[0] == "call"


def test_load_latest_chain_empty(chain_session: Session):
    ts, df = load_latest_chain(chain_session, "NOPE")
    assert ts is None and df.empty


def test_load_snapshot_history_ascending(snap_session: Session):
    t1 = datetime(2026, 5, 21, 20, 0, tzinfo=UTC)
    t2 = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
    snap_session.add_all(
        [
            GreeksSnapshot(symbol="SPY", ts=t2, spot=501.0, gex_total=2.0, source="convex"),
            GreeksSnapshot(symbol="SPY", ts=t1, spot=500.0, gex_total=1.0, source="convex"),
        ]
    )
    snap_session.commit()
    hist = load_snapshot_history(snap_session, "SPY")
    assert list(hist["ts"]) == [pd.Timestamp("2026-05-21 20:00"), pd.Timestamp("2026-05-22 20:00")]
    assert list(hist["gex_total"]) == [1.0, 2.0]
    assert latest_snapshot(snap_session, "SPY").spot == pytest.approx(501.0)


def test_load_quotes_ascending(quote_session: Session):
    quote_session.add_all(
        [
            QuoteDaily(symbol="SPY", date=date(2026, 5, 22), open=1, high=2, low=0,
                       close=1.5, volume=10),
            QuoteDaily(symbol="SPY", date=date(2026, 5, 21), open=1, high=2, low=0,
                       close=1.2, volume=10),
        ]
    )
    quote_session.commit()
    q = load_quotes(quote_session, "SPY")
    assert list(q["date"]) == [pd.Timestamp(2026, 5, 21), pd.Timestamp(2026, 5, 22)]
    assert load_quotes(quote_session, "NOPE").empty
