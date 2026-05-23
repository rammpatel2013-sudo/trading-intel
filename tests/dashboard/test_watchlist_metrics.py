"""Tests for the watchlist overview metrics."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.watchlist_metrics import (
    DISPLAY_LABELS,
    atm_skew,
    call_put_oi_ratio,
    call_wall_distance,
    format_display,
    gamma_concentration,
    gamma_regime,
    gex_change_since,
    gex_direction,
    load_watchlist_metrics,
    vol_oi_ratio,
)
from trading_intel.memory.models import GreeksChain, GreeksSnapshot


def _chain() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"strike": 95.0, "opt_kind": "put", "expiry": pd.Timestamp("2026-05-22"),
             "gxoi": 5.0, "oi": 200, "volume": 100, "iv": 0.30},
            {"strike": 100.0, "opt_kind": "call", "expiry": pd.Timestamp("2026-05-22"),
             "gxoi": 20.0, "oi": 400, "volume": 800, "iv": 0.22},
            {"strike": 105.0, "opt_kind": "call", "expiry": pd.Timestamp("2026-05-22"),
             "gxoi": 8.0, "oi": 100, "volume": 50, "iv": 0.20},
        ]
    )


def test_call_put_oi_ratio():
    # calls 400+100 = 500 ; puts 200 -> 2.5
    assert call_put_oi_ratio(_chain()) == pytest.approx(2.5)
    assert call_put_oi_ratio(pd.DataFrame()) is None


def test_vol_oi_ratio():
    # vol 100+800+50 = 950 ; oi 200+400+100 = 700
    assert vol_oi_ratio(_chain()) == pytest.approx(950 / 700)


def test_atm_skew_put_minus_call():
    # OTM put near 95 iv=0.30 ; OTM calls near 105 = mean(0.22, 0.20)=0.21 -> +0.09
    skew = atm_skew(_chain(), spot=100.0, wing=0.05)
    assert skew == pytest.approx(0.09, abs=1e-9)


def test_gamma_concentration_band():
    # within +/-3% of 100 = [97,103]: only strike 100 (gxoi 20) qualifies; total |gxoi| 33
    conc = gamma_concentration(_chain(), spot=100.0, band=0.03)
    assert conc == pytest.approx(20.0 / 33.0)


def test_call_wall_distance():
    assert call_wall_distance(105.0, 100.0) == pytest.approx(0.05)
    assert call_wall_distance(None, 100.0) is None


def test_gex_direction_and_change():
    hist = pd.DataFrame(
        {
            "ts": [datetime(2026, 5, 15), datetime(2026, 5, 22)],
            "gex_total": [1000.0, 1500.0],
        }
    )
    assert gex_direction(hist) == "up"
    assert gex_change_since(hist, days=7) == pytest.approx(500.0)
    # Single row -> not enough history.
    assert gex_direction(hist.iloc[:1]) == "n/a"
    assert gex_change_since(hist.iloc[:1], days=7) is None


def test_gamma_regime():
    assert "short gamma" in gamma_regime(spot=90.0, gex_flip=100.0)
    assert "long gamma" in gamma_regime(spot=110.0, gex_flip=100.0)
    assert gamma_regime(None, 100.0) == "n/a"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    GreeksSnapshot.__table__.create(engine)
    GreeksChain.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_load_watchlist_metrics_builds_rows(session: Session):
    ts = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)
    session.add(
        GreeksSnapshot(symbol="SPY", ts=ts, spot=100.0, gex_total=1500.0,
                       gex_flip=98.0, atm_iv=0.21, source="convex")
    )
    for r in _chain().itertuples():
        session.add(
            GreeksChain(symbol="SPY", ts=ts, expiry=r.expiry.date(), strike=r.strike,
                        cp=r.opt_kind[0].upper(), gxoi=r.gxoi, oi=r.oi, volume=r.volume,
                        iv=r.iv, source="convex")
        )
    session.commit()

    df = load_watchlist_metrics(session, ["SPY", "QQQ"])
    assert list(df["symbol"]) == ["SPY", "QQQ"]
    spy = df[df["symbol"] == "SPY"].iloc[0]
    assert spy["gex_total"] == pytest.approx(1500.0)
    assert spy["call_put_oi"] == pytest.approx(2.5)
    assert spy["call_wall"] == pytest.approx(100.0)  # strike with most call gxoi
    assert "long gamma" in spy["gamma_regime"]  # spot 100 > flip 98
    # QQQ has no data -> row exists, metrics None.
    qqq = df[df["symbol"] == "QQQ"].iloc[0]
    assert pd.isna(qqq["gex_total"]) or qqq["gex_total"] is None


def test_format_display_renders_and_renames():
    raw = pd.DataFrame(
        [
            {"symbol": "SPY", "spot": 500.123, "gex_total": 1500000.0, "gex_dir": "up",
             "gex_chg_wk": None, "gamma_regime": "long gamma", "gex_flip": 498.0,
             "atm_iv": 0.215, "call_put_oi": 1.234, "vol_oi": 0.5, "skew": 0.03,
             "call_wall": 505.0, "put_wall": 495.0, "call_wall_dist": 0.01,
             "gamma_conc_3pct": 0.42},
        ]
    )
    out = format_display(raw)
    assert list(out.columns) == list(DISPLAY_LABELS.values())
    row = out.iloc[0]
    assert row["Net GEX"] == "1,500,000"
    assert row["GEX dir"] == "up"
    assert row["ATM IV"] == "21.5%"
    assert row["C/P OI"] == "1.23"
    assert row["ΔGEX (1wk)"] == "n/a"  # None -> n/a
    assert row["gamma-conc +/-3%"] == "42.0%"


def test_format_display_empty():
    out = format_display(pd.DataFrame())
    assert list(out.columns) == list(DISPLAY_LABELS.values())
    assert out.empty
