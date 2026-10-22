from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, OiChainEod
from trading_intel.synthesis.daily_brief_render import _zero_dte_section
from trading_intel.synthesis.zero_dte import build_zero_dte, summarize


def test_summarize_walls_sign_range():
    rows = [(5800, "C", 5e8, 0.15, 1000), (5850, "C", 9e8, 0.14, 2000),
            (5700, "P", 7e8, 0.18, 1500), (5650, "P", 2e8, 0.2, 500)]
    z = summarize(rows, 5790.0)
    assert z["call_wall"] == 5850 and z["put_wall"] == 5700
    assert z["net_gex"] > 0 and z["net_sign"].startswith("long")
    assert z["range"][0] < 5790 < z["range"][1]


def test_build_zero_dte_and_render():
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    GreeksSnapshot.__table__.create(eng)
    s = Session(eng)
    d = date(2026, 10, 2)
    ts = datetime(2026, 10, 2, 8, 35)
    for k, cp, g in ((5800, "C", 5e8), (5850, "C", 9e8), (5700, "P", 2e9)):
        s.add(OiChainEod(symbol="SPX", ts=ts, expiry=d, strike=k, cp=cp, dte=0, oi=1000,
                         iv=0.15, gxoi=g, source="am"))
    s.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 10, 16), strike=6000, cp="C", dte=14,
                     oi=99999, iv=0.15, gxoi=9e12, source="am"))
    s.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=5790.0, source="convex"))
    s.commit()
    z = build_zero_dte(s, "SPX", today=d)
    assert z and z["call_wall"] == 5850 and z["put_wall"] == 5700
    assert z["net_sign"].startswith("short")  # puts outweigh, later expiry ignored
    html = _zero_dte_section({"zero_dte": z})
    assert "0DTE gamma map" in html and "<script" not in html
    assert build_zero_dte(s, "SPX", today=date(2026, 10, 5)) is None
