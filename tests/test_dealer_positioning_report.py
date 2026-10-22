from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, OiChainEod
from trading_intel.scheduler.jobs._script_report import load_script


def test_dealer_positioning_builds_static_html(tmp_path):
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    GreeksSnapshot.__table__.create(eng)
    s = Session(eng)
    for i in range(7):
        ts = datetime(2026, 9, 24) + timedelta(days=i)
        for k in range(5600, 6000, 25):
            for cp in ("C", "P"):
                s.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 10, 16), strike=float(k), cp=cp,
                                 dte=20 - i, oi=1000 + (k % 100) * (i + 1), iv=0.16, gxoi=1.0, source="am"))
        s.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=5800.0, source="convex"))
    s.commit()
    rpt = load_script("dealer_positioning_report")
    path = rpt.build(("SPX",), out_path=str(tmp_path / "dp.html"), session=s)
    html = open(path).read()
    assert "<script" not in html and "Vega" in html and "(prior)" in html
    rows = rpt.summarize(*[rpt._collect(s, "SPX")[k] for k in ("now", "before")])
    assert {r["greek"] for r in rows} == {"gamma", "charm", "vanna", "vega"}
    assert all(r["at_spot_before"] is not None for r in rows)
