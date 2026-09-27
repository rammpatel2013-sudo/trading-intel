"""get_profile ``as_of`` picks the newest chain on/before the day."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.mcp import profile_tool as pt
from trading_intel.memory.models import OiChainEod


def test_latest_oi_ts_respects_as_of() -> None:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    s = Session(engine)
    for d in (datetime(2026, 9, 18), datetime(2026, 9, 25)):
        s.add(OiChainEod(symbol="AAPL", ts=d, expiry=date(2026, 10, 16), strike=340.0,
                         cp="C", source="convex_eod"))
    s.commit()
    assert pt._latest_oi_ts(s, "AAPL") == datetime(2026, 9, 25)
    assert pt._latest_oi_ts(s, "AAPL", date(2026, 9, 24)) == datetime(2026, 9, 18)
    assert pt._latest_oi_ts(s, "AAPL", date(2026, 9, 1)) is None
