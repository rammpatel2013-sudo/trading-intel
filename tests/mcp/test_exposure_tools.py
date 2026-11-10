"""Unit tests for exposure_tools MCP readers (Ship 2: #88 Exposure Board).

Same harness as test_vol_tools.py: SQLite in-memory Session, insert ORM rows,
call the pure tool function and assert the JSON-serialisable shape.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.mcp import exposure_tools as ext
from trading_intel.memory.models import (
    FlowSnapshot,
    GreeksSnapshot,
    LiveGex,
    OiChainEod,
)

_TABLES = (OiChainEod, GreeksSnapshot, LiveGex, FlowSnapshot)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in _TABLES:
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


# ── helpers ───────────────────────────────────────────────────────────


def _seed_chain(session: Session, *, spot: float = 5800.0) -> datetime:
    """Seed OiChainEod + GreeksSnapshot for SPX at ``spot``."""
    ts = datetime(2026, 9, 28, 15, 30)
    session.add(
        GreeksSnapshot(
            symbol="SPX", ts=ts, spot=spot, atm_iv=0.15, gex_total=1.0, source="convex"
        )
    )
    for strike in range(5700, 5901, 25):
        for cp in ("C", "P"):
            dxoi = 100.0 if cp == "C" else -80.0
            gxoi = 50.0 if strike > spot else -30.0
            session.add(
                OiChainEod(
                    symbol="SPX",
                    ts=ts,
                    expiry=date(2026, 10, 18),
                    strike=float(strike),
                    cp=cp,
                    dte=20,
                    oi=5000,
                    iv=0.18,
                    dxoi=dxoi,
                    gxoi=gxoi,
                    source="convex_eod",
                )
            )
    session.commit()
    return ts


def _seed_live_gex_ticks(session: Session, n: int = 60) -> None:
    """Seed ``n`` intraday LiveGex ticks for vol_drift."""
    from datetime import timedelta

    base = 5800.0
    start = datetime(2026, 9, 28, 9, 30)
    for i in range(n):
        session.add(
            LiveGex(
                symbol="SPX",
                ts=start + timedelta(minutes=i),
                strike=5800.0,
                cp="C",
                spot=base + (i % 5) * 0.5 - 1.0,
                source="convex",
            )
        )
    session.add(
        GreeksSnapshot(
            symbol="SPX",
            ts=datetime(2026, 9, 28, 15, 0),
            spot=5800.0,
            atm_iv=0.15,
            gex_total=1.0,
            source="convex",
        )
    )
    session.commit()


def _seed_flow_snapshots(session: Session, days: int = 5) -> None:
    """Seed ``days`` of FlowSnapshot rows."""
    for i in range(days):
        session.add(
            FlowSnapshot(
                symbol="SPX",
                ts=datetime(2026, 9, 24 + i, 15, 55),
                source="convex",
                call_notional=1_000_000.0 + i * 50_000,
                put_notional=800_000.0 + i * 30_000,
                net_premium=200_000.0 + i * 20_000,
            )
        )
    session.commit()


# ── get_exposure_by_strike ──────────────────────────────────────────


def test_exposure_by_strike_empty(session: Session) -> None:
    out = ext.get_exposure_by_strike(session)
    assert out["found"] is False


def test_exposure_by_strike_returns_shape(session: Session) -> None:
    _seed_chain(session)
    out = ext.get_exposure_by_strike(session)
    assert out["found"] is True
    assert out["symbol"] == "SPX"
    assert out["spot"] == 5800.0
    assert "strikes" in out
    assert isinstance(out["strikes"], list)
    assert out["count"] > 0

    # Each strike entry has the expected keys
    s = out["strikes"][0]
    for key in ("strike", "net_dex", "call_dex", "put_dex", "net_gex", "call_gex", "put_gex"):
        assert key in s, f"missing key: {key}"

    # Derived values present
    assert "call_wall" in out
    assert "put_wall" in out
    assert "gex_flip" in out

def test_exposure_by_strike_net_is_call_minus_put(session: Session) -> None:
    """2026-11-10: net_gex was call + put (gross). Net must be call - put; gross kept apart."""
    _seed_chain(session)
    out = ext.get_exposure_by_strike(session)
    for s in out["strikes"]:
        assert abs(s["net_gex"] - (s["call_gex"] - s["put_gex"])) < 0.02
        assert abs(s["gross_gex"] - (s["call_gex"] + s["put_gex"])) < 0.02



# ── get_vol_drift ───────────────────────────────────────────────────


def test_vol_drift_empty(session: Session) -> None:
    out = ext.get_vol_drift(session)
    assert out["found"] is False


def test_vol_drift_returns_shape(session: Session) -> None:
    _seed_live_gex_ticks(session, n=60)
    out = ext.get_vol_drift(session, as_of=date(2026, 9, 28))
    assert out["found"] is True
    assert "intraday_rv_annualized" in out
    assert "atm_iv_0dte" in out
    assert "vol_drift" in out
    assert "drift_label" in out
    assert out["units"] == "annualized vol (decimal)"


# ── get_net_premium_drift ───────────────────────────────────────────


def test_net_premium_drift_empty(session: Session) -> None:
    out = ext.get_net_premium_drift(session)
    assert out["found"] is False


def test_net_premium_drift_returns_shape(session: Session) -> None:
    _seed_flow_snapshots(session, days=5)
    out = ext.get_net_premium_drift(session)
    assert out["found"] is True
    assert "series" in out
    assert isinstance(out["series"], list)
    assert out["count"] == 5
    assert "cum_call_notional" in out
    assert "cum_put_notional" in out
    assert "cum_net" in out
    assert "bias" in out
    assert out["bias"] == "call-heavy"  # call > put in our seed
