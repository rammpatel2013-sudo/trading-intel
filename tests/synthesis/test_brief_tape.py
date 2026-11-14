"""Tape pillar end to end on SQLite (2026-11-13 overnight): context + HTML never crash."""
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import BreadthSnapshot, QuoteDaily, Ticker, VixData
from trading_intel.synthesis import brief_tape
from trading_intel.synthesis.brief_tape_render import tape_section


def _db() -> Session:
    eng = create_engine("sqlite://")
    for t in (Ticker, QuoteDaily, VixData, BreadthSnapshot):
        t.__table__.create(eng)
    return Session(eng)


def test_tape_context_and_render():
    s = _db()
    d0 = date(2026, 6, 1)
    for sym in ("SPX", "SPY"):
        s.add(Ticker(symbol=sym))
    for i in range(120):
        d = d0 + timedelta(days=i)
        px = 7000 + 5 * i - (300 if 100 <= i < 105 else 0)
        s.add(QuoteDaily(id=2 * i + 1, symbol="SPX", date=d, open=px, high=px + 20, low=px - 20, close=px, volume=1))
        s.add(QuoteDaily(id=2 * i + 2, symbol="SPY", date=d, open=px / 10, high=px / 10, low=px / 10, close=px / 10,
                         volume=1_000_000 + (3_000_000 if i == 104 else 0)))
        s.add(VixData(date=d, vix=15.0 + (12 if i == 100 else 0), vix9d=14.0, vix3m=17.0))
        s.add(BreadthSnapshot(id=i + 1, ts=d, source="fmp_sp500", advancers=300, decliners=200, pct_above_50=60,
                              new_highs=20, new_lows=5, up4=10, down4=3))
    s.commit()
    ctx = brief_tape.tape_context(s)
    assert ctx["checklist"] and ctx["posture"]["word"] in ("press", "selective", "stand aside")
    assert ctx["board"][0]["symbol"] == "SPX"
    html = tape_section({"tape": ctx})
    assert "Bottom checklist" in html and "Posture" in html


def test_empty_db_is_safe():
    s = _db()
    ctx = brief_tape.tape_context(s)
    assert ctx["posture"]["word"] == "selective"
    assert "Tape pillar" in tape_section({"tape": ctx})
