"""0DTE Sell Gate (2026-11-22)."""
from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import sell_gate as g
from trading_intel.memory.models import GreeksSnapshot, IndexSkewDaily, VixData, VolSurfaceCM


def test_count_and_bucket() -> None:
    assert g.count_signs({"a": True, "b": False, "c": None, "d": None}) is None
    assert g.count_signs({"a": True, "b": False, "c": True, "d": None}) == 2
    assert g.bucket(0) == "sell" and g.bucket(2) == "caution" and g.bucket(3) == "stop" and g.bucket(None) == "unknown"


def test_front_vol_rising() -> None:
    assert g.front_vol_rising([1, 2, 3, 4, 5]) is None
    assert g.front_vol_rising([0.10, 0.12, 0.11, 0.13, 0.14, 0.20]) is True
    assert g.front_vol_rising([0.10, 0.12, 0.11, 0.13, 0.21, 0.20]) is False  # down vs 1 day back


def test_gate_for_session_uses_prior_closes_only() -> None:
    eng = create_engine("sqlite://")
    for m in (GreeksSnapshot, IndexSkewDaily, VixData, VolSurfaceCM):
        m.__table__.create(eng)
    s = Session(eng)
    days = [date(2026, 11, d) for d in (13, 16, 17, 18, 19, 20)]
    for i, d in enumerate(days):
        s.add(VolSurfaceCM(symbol="SPX", ts=d, dte=7, delta=50.0, side="call", iv=0.15 + 0.05 * i))
    s.add(VolSurfaceCM(symbol="SPX", ts=date(2026, 11, 23), dte=7, delta=50.0, side="call", iv=0.01))  # same day: ignored
    s.add(GreeksSnapshot(symbol="SPX", ts=datetime(2026, 11, 20, 17, 0), gex_total=-150.0, spot=6910.0, gex_flip=7515.0, source="convex"))
    s.add(VixData(date=date(2026, 11, 20), vix=35.89))
    s.add(IndexSkewDaily(date=date(2026, 11, 20), spx_rr_pctile_252d=0.96))
    s.flush()
    out = g.gate_for_session(s, date(2026, 11, 23))
    assert out["count"] == 4 and out["bucket"] == "stop"
    assert all(out["signs"].values())
