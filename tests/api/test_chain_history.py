"""oi_chain_daily reader — ordering, P/C ratio, not-found."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.api.chain_history import build_chain_history
from trading_intel.memory.models import OiChainDaily


def _session() -> Session:
    engine = create_engine("sqlite://")
    OiChainDaily.__table__.create(engine)
    return Session(engine)


def test_chain_history_oldest_first_with_pc_ratio() -> None:
    s = _session()
    for i, d in enumerate([date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 23)]):
        s.add(OiChainDaily(symbol="AAPL", ts=d, call_oi=100 + i, put_oi=50, call_wall=340.0))
    s.commit()
    out = build_chain_history(s, "aapl", days=2)
    assert out["found"] and out["n"] == 2
    assert [p["date"] for p in out["series"]] == ["2026-09-24", "2026-09-25"]
    assert round(out["series"][0]["pc_oi_ratio"], 3) == 0.5


def test_chain_history_not_found() -> None:
    assert build_chain_history(_session(), "ZZZ") == {"symbol": "ZZZ", "found": False}
