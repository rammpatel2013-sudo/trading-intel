"""Own IVs for the 08:35 book in the MCP readers (2026-11-21): a broken vendor IV no longer makes a fake wall."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import clean_book as cb
from trading_intel.market.clean_iv import book_fix
from trading_intel.memory.models import GreeksSnapshot, OiChainEod

DAY = date(2026, 11, 13)
TS = datetime(2026, 11, 13)
EXP = date(2026, 11, 18)
F = 7560.0
SIG = 0.15


def _book(session: Session, source: str, broken: float = 7860.0) -> None:
    t = cb.years(cb.book_time("am", DAY), cb.settle_dt(EXP, False))
    for k in np.arange(7300.0, 7900.0, 10.0):
        for cp in ("C", "P"):
            px = float(cb.b76(F, np.array([k]), np.array([SIG]), t, np.array([cp == "C"]))[0])
            gamma = float(cb.b76_gamma(F, np.array([k]), np.array([SIG]), t)[0])
            vendor_iv = 0.0023 if (k == broken and cp == "C") else SIG
            vg = float(cb.b76_gamma(F, np.array([k]), np.array([vendor_iv]), t)[0])
            oi = 4000.0 if (k == broken and cp == "C") else (40000.0 if k == 7800.0 and cp == "C" else 1000.0)
            session.add(OiChainEod(
                symbol="SPX", ts=TS, expiry=EXP, strike=float(k), cp=cp, dte=5, oi=int(oi), iv=vendor_iv,
                gamma=vg, gxoi=(5e9 if vendor_iv < 0.01 else gamma * oi * 100), dxoi=0.0,
                bid_price=max(px - 0.05, 0.01), ask_price=px + 0.05, source=source))
    session.add(GreeksSnapshot(symbol="SPX", ts=TS + timedelta(hours=9), spot=F, source="convex"))
    session.commit()


@pytest.fixture
def am_session():
    e = create_engine("sqlite://")
    OiChainEod.__table__.create(e)
    GreeksSnapshot.__table__.create(e)
    with Session(e) as s:
        _book(s, "convex_am")
        yield s


def test_book_fix_own_iv_and_scales(am_session):
    fx = book_fix(am_session, "SPX", TS)
    assert fx is not None
    assert fx.own_iv(EXP, 7860.0) == pytest.approx(SIG, abs=0.01)
    assert fx.iv(EXP, 7860.0, 0.0023) == pytest.approx(SIG, abs=0.01)
    assert fx.n_big == 1
    assert fx.unit_g == pytest.approx(100.0, rel=1e-3)        # calibrated on the clean near-forward strikes
    t = cb.years(cb.book_time("am", DAY), cb.settle_dt(EXP, False))
    g_true = float(cb.b76_gamma(F, np.array([7860.0]), np.array([SIG]), t)[0]) * 4000 * 100
    assert fx.gxoi(EXP, 7860.0, "C", 4000, 9.9e9) == pytest.approx(g_true, rel=0.05)   # vendor's huge number replaced
    assert fx.gxoi(EXP, 9000.0, "C", 10, 5.0) == 5.0         # no own IV -> vendor kept
    assert fx.own_iv(EXP, 9000.0) is None                    # outside the smile -> vendor kept
    assert fx.iv(EXP, 9000.0, 0.2) == 0.2


def test_eod_book_untouched():
    e = create_engine("sqlite://")
    OiChainEod.__table__.create(e)
    GreeksSnapshot.__table__.create(e)
    with Session(e) as s:
        _book(s, "convex_eod")
        assert book_fix(s, "SPX", TS) is None


def test_walls_no_fake_call_wall(am_session, monkeypatch):
    from trading_intel.mcp import extra_tools as et

    monkeypatch.setattr(et, "_expiry_floor", lambda ts: DAY)
    w = et.get_walls(am_session, "SPX", dte_max=7)
    assert w["call_wall"] == 7800.0
    assert "own IVs" in w["iv_source"]
    monkeypatch.setattr(et, "book_fix", lambda *a, **k: None)
    assert et.get_walls(am_session, "SPX", dte_max=7)["call_wall"] == 7860.0   # what the vendor IV gave
