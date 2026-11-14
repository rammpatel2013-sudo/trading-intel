"""Vol read loader + report render on SQLite (2026-11-13 overnight)."""
from datetime import date, timedelta
import importlib.util
import pathlib

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import IvTenorSnapshot, QuoteDaily, SqueezeMetricsDaily, Ticker, VixData
from trading_intel.synthesis.vol_read import brief_line, build


def test_build_and_render():
    eng = create_engine("sqlite://")
    for t in (Ticker, QuoteDaily, IvTenorSnapshot, VixData, SqueezeMetricsDaily):
        t.__table__.create(eng)
    s = Session(eng)
    s.add(Ticker(symbol="SPX"))
    d, k = date(2026, 6, 1), 0
    while k < 80:
        if d.weekday() < 5:
            px = 7000 + 3 * k - (150 if k > 70 else 0)
            s.add(QuoteDaily(id=k + 1, symbol="SPX", date=d, open=px, high=px, low=px, close=px, volume=1))
            s.add(IvTenorSnapshot(id=k + 1, symbol="SPX", ts=d, tenor_dte=30, iv_atm=0.13 + (0.03 if k > 70 else 0),
                                  iv_put_25d=0.16, iv_call_25d=0.11))
            s.add(VixData(date=d, vix=16.0 + (6 if k > 72 else 0), vix9d=15.0 + (9 if k > 72 else 0), vix3m=18.0))
            s.add(SqueezeMetricsDaily(date=d, gex=1e9 * (1 + k % 7)))
            k += 1
        d += timedelta(days=1)
    s.commit()
    vr = build(s)
    assert vr["found"] and vr["verdict"]["of"] == 8
    assert "Vol read:" in brief_line(vr)
    spec = importlib.util.spec_from_file_location("vrr", pathlib.Path("scripts/vol_read_report.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert "SPX vol read" in m.render(vr)
