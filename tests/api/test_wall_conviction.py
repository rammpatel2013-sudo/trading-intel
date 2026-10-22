"""wall-conviction reader on a tiny sqlite chain."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.api.wall_conviction import build_wall_conviction, render_section
from trading_intel.memory.models import EarningsEvent, GreeksSnapshot, OiChainEod


def _session() -> Session:
    eng = create_engine("sqlite://")
    for m in (OiChainEod, GreeksSnapshot, EarningsEvent):
        m.__table__.create(eng)
    return Session(eng)


def _seed(s: Session, *, call_iv_path, spot=104.6, earn=None) -> None:
    exp = date(2026, 10, 16)
    for i, iv in enumerate(call_iv_path):
        ts = datetime(2026, 9, 28) + timedelta(days=i)
        for k, cp, g, oi, v in ((105.0, "C", 9e6, 9000, 3000), (110.0, "C", 1e6, 500, 50),
                                (95.0, "P", 8e6, 8000, 2500), (90.0, "P", 1e6, 400, 40)):
            s.add(OiChainEod(symbol="XYZ", ts=ts, expiry=exp, strike=k, cp=cp, dte=18 - i,
                             oi=oi, volume=v, iv=iv if (k == 105.0) else 0.40, gxoi=g))
        s.add(GreeksSnapshot(symbol="XYZ", ts=ts, spot=spot, gex_total=1e9 * (i - 1),
                             atm_iv=0.4, source="convex"))
    if earn:
        s.add(EarningsEvent(symbol="XYZ", date=earn))
    s.commit()


def test_call_wall_hold_when_vol_calm() -> None:
    s = _session()
    _seed(s, call_iv_path=[0.40, 0.40, 0.399, 0.398])
    d = build_wall_conviction(s, "xyz", today=date(2026, 10, 1))
    cw = d["walls"]["call"]
    assert cw["strike"] == 105.0 and cw["verdict"] == "HOLD"
    assert d["walls"]["put"]["proximity"] == "far"
    html = render_section(d)
    assert "<script" not in html and "Wall conviction" in html and "Rolling net GEX" in html


def test_call_wall_break_when_vol_bid_and_wait_into_earnings() -> None:
    s = _session()
    _seed(s, call_iv_path=[0.40, 0.41, 0.42, 0.43])
    assert build_wall_conviction(s, "XYZ", today=date(2026, 10, 1))["walls"]["call"]["verdict"] == "BREAK"
    s2 = _session()
    _seed(s2, call_iv_path=[0.40, 0.41, 0.42, 0.43], earn=date(2026, 10, 5))
    assert build_wall_conviction(s2, "XYZ", today=date(2026, 10, 1))["walls"]["call"]["verdict"] == "WAIT"
