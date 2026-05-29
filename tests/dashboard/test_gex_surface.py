"""Tests for the GEX-surface (strike x time) pure helpers — SQLite, no Postgres."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.gex_surface import (
    _expiry_within,
    gex_strike_matrix,
    latest_strike_profiles,
    load_gex_strike_series,
    spot_flip_overlay,
)
from trading_intel.memory.models import GreeksChain, GreeksSnapshot

_NEAR = date(2026, 6, 18)
_FAR = date(2026, 12, 18)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    # Only the tables these helpers touch (avoids the chunks ARRAY column).
    GreeksChain.__table__.create(engine)
    GreeksSnapshot.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add_chain(session: Session, ts: datetime, *, expiry: date = _NEAR, scale: float = 1.0) -> None:
    """Two strikes: a call (positive gxoi) and a put (negative net after sign)."""
    session.add(
        GreeksChain(symbol="SPX", ts=ts, expiry=expiry, strike=7500, cp="C",
                    gxoi=100.0 * scale, source="convex")
    )
    session.add(
        GreeksChain(symbol="SPX", ts=ts, expiry=expiry, strike=7400, cp="P",
                    gxoi=40.0 * scale, source="convex")
    )
    session.commit()


def test_long_frame_shape_and_sign(session: Session):
    t1 = datetime(2026, 5, 21, 6, 45, tzinfo=UTC)
    t2 = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    _add_chain(session, t1, scale=1.0)
    _add_chain(session, t2, scale=2.0)

    series = load_gex_strike_series(session, "SPX", days=30)
    assert list(series.columns) == ["ts", "strike", "net_gex"]
    assert series["ts"].nunique() == 2
    assert len(series) == 4  # 2 strikes x 2 snapshots

    # SQLite drops tzinfo; compare against the naive timestamp it stored back.
    t2_naive = t2.replace(tzinfo=None)
    latest = series[series["ts"] == t2_naive]
    # calls add positively, puts subtract (project GEX convention).
    call_t2 = latest[latest["strike"] == 7500]["net_gex"].iloc[0]
    put_t2 = latest[latest["strike"] == 7400]["net_gex"].iloc[0]
    assert call_t2 == pytest.approx(200.0)
    assert put_t2 == pytest.approx(-80.0)


def test_matrix_pivot_shape(session: Session):
    _add_chain(session, datetime(2026, 5, 21, 6, 45, tzinfo=UTC))
    _add_chain(session, datetime(2026, 5, 22, 6, 45, tzinfo=UTC))

    matrix = gex_strike_matrix(load_gex_strike_series(session, "SPX", days=30))
    assert matrix.shape == (2, 2)  # 2 strikes x 2 timestamps
    assert list(matrix.index) == [7400.0, 7500.0]  # sorted strikes


def test_expiry_within_filter_drops_far_dated(session: Session):
    ts = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    _add_chain(session, ts, expiry=_NEAR)
    # A far-dated strike that a near-term view must exclude.
    session.add(
        GreeksChain(symbol="SPX", ts=ts, expiry=_FAR, strike=8000, cp="C",
                    gxoi=999.0, source="convex")
    )
    session.commit()

    near = load_gex_strike_series(session, "SPX", days=30, expiry_within_days=60)
    assert 8000.0 not in set(near["strike"])
    wide = load_gex_strike_series(session, "SPX", days=30, expiry_within_days=365)
    assert 8000.0 in set(wide["strike"])


def test_pct_range_trims_to_near_the_money(session: Session):
    ts = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    _add_chain(session, ts, expiry=_NEAR)  # strikes 7400 / 7500
    # A far-OTM strike well outside +/-3% of a 7450 spot.
    session.add(
        GreeksChain(symbol="SPX", ts=ts, expiry=_NEAR, strike=9000, cp="C",
                    gxoi=999.0, source="convex")
    )
    # Spot for the band comes from greeks_snapshots, matched by day.
    session.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=7450.0))
    session.commit()

    near = load_gex_strike_series(session, "SPX", days=30, pct_range=0.03)
    assert set(near["strike"]) == {7400.0, 7500.0}  # 9000 trimmed (band ~7227..7674)
    full = load_gex_strike_series(session, "SPX", days=30, pct_range=None)
    assert 9000.0 in set(full["strike"])


def test_pct_range_keeps_all_when_no_spot(session: Session):
    ts = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    _add_chain(session, ts, expiry=_NEAR)
    # No greeks_snapshots row -> no spot for the day -> filter is skipped.
    series = load_gex_strike_series(session, "SPX", days=30, pct_range=0.03)
    assert set(series["strike"]) == {7400.0, 7500.0}


def test_expiry_within_handles_tz_aware_ts():
    # Regression: Postgres returns tz-aware ts; expiry is a tz-naive date.
    # Subtracting them directly raises — _expiry_within must compare dates.
    chain = pd.DataFrame(
        {
            "strike": [7400, 7500],
            "opt_kind": ["call", "put"],
            "expiry": [pd.Timestamp("2026-06-18"), pd.Timestamp("2026-12-18")],
            "gxoi": [1.0, 2.0],
        }
    )
    ts_aware = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    kept = _expiry_within(chain, ts_aware, 60)
    assert set(kept["strike"]) == {7400}  # near-dated kept, far-dated dropped


def test_empty_when_no_chain(session: Session):
    series = load_gex_strike_series(session, "SPX", days=30)
    assert series.empty
    assert list(series.columns) == ["ts", "strike", "net_gex"]
    assert gex_strike_matrix(series).empty


def test_spot_flip_overlay(session: Session):
    t1 = datetime(2026, 5, 21, 16, 30, tzinfo=UTC)
    t2 = datetime(2026, 5, 22, 16, 30, tzinfo=UTC)
    session.add(GreeksSnapshot(symbol="SPX", ts=t1, spot=7480.0, gex_flip=7450.0))
    session.add(GreeksSnapshot(symbol="SPX", ts=t2, spot=7510.0, gex_flip=7470.0))
    session.commit()

    overlay = spot_flip_overlay(session, "SPX", days=30)
    assert list(overlay.columns) == ["ts", "spot", "gex_flip"]
    assert len(overlay) == 2
    # oldest first
    assert overlay["spot"].tolist() == [7480.0, 7510.0]


def test_latest_strike_profiles_merges_four(session: Session):
    ts = datetime(2026, 5, 22, 6, 45, tzinfo=UTC)
    # one call + one put at the same strike, full greek set
    session.add(GreeksChain(symbol="SPX", ts=ts, expiry=_NEAR, strike=7500, cp="C",
                            gxoi=100.0, dxoi=50.0, vxoi=10.0, oi=4000, source="convex"))
    session.add(GreeksChain(symbol="SPX", ts=ts, expiry=_NEAR, strike=7500, cp="P",
                            gxoi=40.0, dxoi=-30.0, vxoi=6.0, oi=1000, source="convex"))
    session.commit()

    prof = latest_strike_profiles(session, "SPX", pct_range=None)
    assert list(prof.columns) == ["strike", "oi", "gex", "vanna", "delta"]
    row = prof[prof["strike"] == 7500.0].iloc[0]
    assert row["oi"] == pytest.approx(5000.0)          # total OI (unsigned)
    assert row["gex"] == pytest.approx(60.0)           # 100 - 40 (calls + / puts -)
    assert row["vanna"] == pytest.approx(4.0)          # 10 - 6 (vanna signed like gamma)
    assert row["delta"] == pytest.approx(20.0)         # dxoi summed as-is: 50 + (-30)


def test_latest_strike_profiles_empty(session: Session):
    assert latest_strike_profiles(session, "NOPE").empty
