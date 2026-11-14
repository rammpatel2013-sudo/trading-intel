"""Sunday edition (2026-11-13 overnight): builds and sends on an empty-ish DB without crashing."""
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import BreadthSnapshot, QuoteDaily, Ticker, VixData
from trading_intel.scheduler.jobs import sunday_edition


class _Tele:
    def __init__(self):
        self.sent = []

    def send_message(self, m):
        self.sent.append(m)
        return True

    def send_document(self, p, caption=""):
        self.sent.append(str(p))
        return True


def test_sunday_edition_builds(tmp_path, monkeypatch):
    eng = create_engine("sqlite://")
    for t in (Ticker, QuoteDaily, VixData, BreadthSnapshot):
        t.__table__.create(eng)
    s = Session(eng)
    s.add(Ticker(symbol="SPX"))
    d0 = date(2026, 11, 2)
    for i in range(12):
        s.add(QuoteDaily(id=i + 1, symbol="SPX", date=d0 + timedelta(days=i), open=7000, high=7010,
                         low=6990, close=7000 + i, volume=1))
        s.add(BreadthSnapshot(id=i + 1, ts=d0 + timedelta(days=i), source="fmp_sp500", pct_above_50=50 + i,
                              pct_above_200=60, ad_line=float(i * 10), new_highs=5, new_lows=2))
    s.commit()
    monkeypatch.setattr(sunday_edition, "_OUT", tmp_path)
    t = _Tele()
    out = sunday_edition.run(s, settings=object(), client=t)
    assert out["delivered"] and "posture" in t.sent[0].lower()
    assert "Sunday edition" in (tmp_path / out["file"].split("/")[-1]).read_text()
