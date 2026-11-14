"""Mag7 views + report render on SQLite (2026-11-13 overnight; #91 #92 #93)."""
import importlib.util
import pathlib
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import mag7_views as mv
from trading_intel.memory.models import EarningsEvent, OiChainEod, QuoteDaily, TasDailyFlow, Ticker


def test_views_and_render():
    eng = create_engine("sqlite://")
    for t in (Ticker, QuoteDaily, OiChainEod, EarningsEvent, TasDailyFlow):
        t.__table__.create(eng)
    s = Session(eng)
    s.add(Ticker(symbol="NVDA"))
    d0, k, q = date(2026, 9, 1), 0, 0
    for i in range(70):
        d = d0 + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        q += 1
        px = 180 + (i % 9)
        s.add(QuoteDaily(id=q, symbol="NVDA", date=d, open=px, high=px, low=px, close=px, volume=1))
        ts = datetime.combine(d, datetime.min.time()).replace(hour=17, minute=15)
        for dte, exp in ((7, d + timedelta(days=7)), (30, d + timedelta(days=30))):
            for kk in (px - 5, px, px + 5):
                for cp in "CP":
                    k += 1
                    s.add(OiChainEod(id=k, symbol="NVDA", ts=ts, expiry=exp, strike=float(kk), cp=cp, dte=dte,
                                     oi=100, iv=0.5, gxoi=1000.0))
        s.add(TasDailyFlow(id=q, trade_date=d, root="NVDA", call_notional=2e6, put_notional=1e6))
    s.add(EarningsEvent(id=1, symbol="NVDA", date=date(2026, 9, 20)))
    s.commit()
    wk = mv.weekly_moves(s, "NVDA")
    assert wk and wk[0]["implied_pct"] > 0
    g = mv.gamma_grid(s, "NVDA")
    assert g["found"] and len(g["expiries"]) == 2
    iv = mv.iv_history(s, "NVDA")
    assert iv["found"] and iv["points"][0]["iv"] == 50.0
    spec = importlib.util.spec_from_file_location("m7", pathlib.Path("scripts/mag7_report.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    html = m.render({"NVDA": {"weekly": wk, "gamma": g, "iv": iv}})
    assert "<svg" in html and "NVDA" in html
