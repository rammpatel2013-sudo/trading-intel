"""Unit tests for vol_tools MCP readers (Ship 2: #87, #90, #94, #95).

Same harness as test_extra_tools.py: SQLite in-memory Session, insert ORM rows,
call the pure tool function and assert the JSON-serialisable shape.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.mcp import vol_tools as vt
from trading_intel.memory.models import (
    GreeksSnapshot,
    IndexSkewDaily,
    OiChainEod,
    QuoteDaily,
    VixData,
)

_TABLES = (QuoteDaily, VixData, IndexSkewDaily, GreeksSnapshot, OiChainEod)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in _TABLES:
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


# ── helpers ───────────────────────────────────────────────────────────


def _seed_spx_quotes(session: Session, n: int = 60) -> None:
    """Insert ``n`` days of synthetic SPX quotes with rv20/rv60."""
    base = 5800.0
    for i in range(n):
        d = date(2026, 8, 1) + timedelta(days=i)
        px = base + i * 2
        session.add(
            QuoteDaily(
                symbol="SPX",
                date=d,
                open=px - 1,
                high=px + 5,
                low=px - 5,
                close=px,
                volume=1_000_000 + i,
                rv20=0.12 + i * 0.0001,
                rv60=0.14 + i * 0.00005,
            )
        )
    session.commit()


def _seed_vix(session: Session, n: int = 60) -> None:
    """Insert ``n`` days of synthetic VIX data."""
    for i in range(n):
        d = date(2026, 8, 1) + timedelta(days=i)
        session.add(
            VixData(
                date=d,
                vix=18.0 + i * 0.05,
                vvix=90.0,
                vix9d=16.0,
                vix3m=20.0 + i * 0.02,
                vix6m=22.0,
                vrp=6.0,
            )
        )
    session.commit()


def _seed_index_skew(session: Session, n: int = 60) -> None:
    """Insert ``n`` days of synthetic index_skew_daily data with correlation fields."""
    for i in range(n):
        d = date(2026, 8, 1) + timedelta(days=i)
        session.add(
            IndexSkewDaily(
                date=d,
                cor1m=0.40 + i * 0.002,
                cor1m_pctile_252d=0.45,
                cor3m=0.42 + i * 0.001,
                cor3m_pctile_252d=0.50,
                vixeq=20.0,
                vixeq_pctile_252d=0.55,
                dspx=15.0,
                dspx_pctile_252d=0.40,
                vixeq_vix_spread=2.0,
                vix_spx_beta_60d=-3.5,
            )
        )
    session.commit()


# ── #94 Carroll's RV/IV box ──────────────────────────────────────────


def test_rv_iv_box_empty(session: Session) -> None:
    out = vt.get_rv_iv_box(session)
    assert out["found"] is False


def test_rv_iv_box_returns_shape(session: Session) -> None:
    _seed_spx_quotes(session)
    _seed_vix(session)
    session.add(
        GreeksSnapshot(
            symbol="SPX",
            ts=datetime(2026, 9, 28),
            spot=5918.0,
            atm_iv=0.15,
            gex_total=1.0,
            source="convex",
        )
    )
    session.commit()

    out = vt.get_rv_iv_box(session)
    assert out["found"] is True
    assert "rv20" in out
    assert "vix" in out
    assert "vix3m_proxy_vx30" in out
    assert "vrp_pts" in out
    assert "series" in out
    assert isinstance(out["series"], list)
    assert out["count"] > 0
    assert "note" in out


# ── #90 Spot-vol beta ────────────────────────────────────────────────


def test_spot_vol_beta_empty(session: Session) -> None:
    out = vt.get_spot_vol_beta(session)
    assert out["found"] is False


def test_spot_vol_beta_returns_shape(session: Session) -> None:
    _seed_spx_quotes(session)
    _seed_vix(session)
    _seed_index_skew(session)

    out = vt.get_spot_vol_beta(session)
    assert out["found"] is True
    assert "beta" in out
    assert "window" in out
    assert out["window"] == 21
    assert "residual_today" in out
    assert "banked_beta_60d" in out
    assert "series" in out
    assert isinstance(out["series"], list)


# ── #95 Implied correlation ──────────────────────────────────────────


def test_implied_correlation_empty(session: Session) -> None:
    out = vt.get_implied_correlation(session)
    assert out["found"] is False


def test_implied_correlation_returns_shape(session: Session) -> None:
    _seed_index_skew(session)

    out = vt.get_implied_correlation(session)
    assert out["found"] is True
    assert "cor1m" in out
    assert "cor3m" in out
    assert "cor_slope" in out
    assert "vixeq" in out
    assert "dspx" in out
    assert "regime" in out
    assert "series" in out
    assert isinstance(out["series"], list)
    assert out["count"] > 0


# ── #87 Color at spot ────────────────────────────────────────────────


def test_color_at_spot_empty(session: Session) -> None:
    out = vt.get_color_at_spot(session, "SPX")
    assert out["found"] is False


def test_color_at_spot_returns_shape(session: Session) -> None:
    ts = datetime(2026, 9, 28, 10, 0)
    session.add(
        GreeksSnapshot(
            symbol="SPX", ts=ts, spot=5800.0, gex_total=1.0, source="convex"
        )
    )
    for strike in range(5700, 5901, 25):
        for cp in ("C", "P"):
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
                    source="convex_eod",
                )
            )
    session.commit()

    out = vt.get_color_at_spot(session, "SPX")
    assert out["found"] is True
    assert "gamma_now_bn" in out
    assert "gamma_later_bn" in out
    assert "color_bn" in out
    assert "color_sign" in out
    assert out["units"] == "$bn per 1% move"


def test_rv_iv_box_ratio_uses_1m_constant_maturity_iv(session: Session) -> None:
    """2026-11-13 data check D4: IV/RV uses the 30-day constant-maturity ATM IV, not the
    front-expiry atm_iv (weekday-biased)."""
    from trading_intel.memory.models import IvTenorSnapshot, Ticker

    Ticker.__table__.create(session.get_bind(), checkfirst=True)
    IvTenorSnapshot.__table__.create(session.get_bind(), checkfirst=True)
    _seed_spx_quotes(session)
    session.add(GreeksSnapshot(symbol="SPX", source="convex", ts=datetime(2026, 9, 28, 17, 15),
                               spot=7000.0, atm_iv=0.08))
    session.add(IvTenorSnapshot(symbol="SPX", ts=date(2026, 9, 28), tenor_dte=30, iv_atm=0.15))
    session.commit()
    out = vt.get_rv_iv_box(session)
    rv20 = out["rv20"]
    assert out["iv_1m"] == 0.15 and "constant-maturity" in out["iv_rv_basis"]
    assert out["iv_rv_ratio"] == round(0.15 / rv20, 2)
    assert out["atm_iv"] == 0.08
