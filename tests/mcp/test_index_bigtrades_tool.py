"""index_bigtrades_tool: same-second merge, size-matched structures, financing rule (2026-11-21)."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.mcp import index_bigtrades_tool as ibt
from trading_intel.memory.models import TasPrint
from trading_intel.scheduler.jobs.tas_capture_job import _is_financing

DAY = date(2026, 11, 17)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    TasPrint.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _p(s, ms, exp, k, cp, size, price, delta, lg, side="sell", sec=47):
    s.add(TasPrint(captured_at=datetime(2026, 11, 17, 11, 5), ts=datetime(2026, 11, 17, 11, 4, sec, ms * 1000),
                   trade_date=DAY, symbol=f".SPX{exp:%y%m%d}{cp}{int(k)}", root="SPX", expiry=exp, strike=k,
                   cp=cp, side=side, price=price, size=size, notional=price * size * 100, delta=delta,
                   source="convex_index", leg_group=lg, is_financing=True))


def test_split_block_is_merged_and_not_glued_to_unrelated_leg(session: Session):
    nov20, dec18 = date(2026, 11, 20), date(2026, 12, 18)
    for ms, size, lg in [(903, 1300, "g1"), (903, 1000, "g1"), (904, 1300, "g2"), (904, 900, "g2"),
                         (905, 5200, "g3"), (905, 1000, "g3"), (905, 500, "g3"), (906, 1913, "g4")]:
        _p(session, ms, nov20, 7500.0, "P", size, 167.8, -0.87, lg)
    _p(session, 905, dec18, 7320.0, "C", 4576, 193.0, 0.54, "g3", side="buy")
    session.commit()
    out = ibt.get_index_bigtrades(session, trade_date=DAY.isoformat())
    top = out["outrights"][0]
    assert (top["strike"], top["cp"], top["size"], top["prints"]) == (7500.0, "P", 13113, 8)
    assert top["is_financing"] is False                     # short-dated real flow, not financing
    assert out["structures"] == []                          # 13,113 vs 4,576 is not one structure


def test_size_matched_roll_stays_a_structure(session: Session):
    d4, d31 = date(2026, 12, 4), date(2026, 12, 31)
    _p(session, 351, d4, 6350.0, "P", 43120, 25.4, -0.09, "r")
    _p(session, 351, d31, 5900.0, "P", 43120, 36.2, -0.08, "r")
    _p(session, 351, d31, 6000.0, "P", 43120, 41.4, -0.09, "r")
    session.commit()
    s = ibt.get_index_bigtrades(session, trade_date=DAY.isoformat())["structures"]
    assert len(s) == 1 and s[0]["n_legs"] == 3 and s[0]["financing"] is False


@pytest.mark.parametrize("delta,dte,fin", [(-0.87, 3, False), (-0.98, 0, True), (0.9, 45, True),
                                           (0.75, 400, True), (0.6, 400, False)])
def test_financing_rule(delta, dte, fin):
    assert _is_financing("P" if delta < 0 else "C", delta, dte) is fin
