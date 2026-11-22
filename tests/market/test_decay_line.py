"""decay_line maths + the background scorer job (2026-11-21)."""
from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import decay_line as DL
from trading_intel.memory.models import OiChainEod, PosLedger, TasPrint
from trading_intel.memory.models_equity import EquityBar
from trading_intel.memory.models_positions import DecayLineMark
from trading_intel.scheduler.jobs import decay_line_score as J

MON, FRI = date(2026, 11, 16), date(2026, 11, 20)


def test_dealer_short_put_block_flips_buy_above_sell_below():
    """VolSignals' week: dealers short ~21k 7,500 puts -> decay buys above 7,500, sells below."""
    c = [DL.Contract(7500.0, False, 21000, 0.2, datetime(2026, 11, 20, 9, 30), None)]
    grid = np.arange(7300.0, 7705.0, 5.0)
    curve = DL.decay_curve(c, DL.dealer_positions(c, 7560.0)["N"], grid, datetime(2026, 11, 16, 10))
    assert curve[grid.tolist().index(7600.0)] > 0 and curve[grid.tolist().index(7400.0)] < 0
    assert DL.flips(list(grid), list(curve)) == pytest.approx([7500.0], abs=5)


def test_ledger_sign_replaces_naive_where_known():
    c = [DL.Contract(7500.0, False, 1000, 0.2, datetime(2026, 11, 20, 16), 300.0),   # customers net short 300?
         DL.Contract(7000.0, False, 5000, 0.2, datetime(2026, 11, 20, 9, 30), None)]  # AM, outside +-2.5%
    p = DL.dealer_positions(c, 7500.0)
    assert p["N"].tolist() == [-1000, -5000]
    assert p["W"].tolist() == [-1000, 0]
    assert p["L"].tolist() == [-300, 0]     # dealer = -customer net from the ledger


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for t in (OiChainEod, PosLedger, TasPrint, EquityBar, DecayLineMark):
        t.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_job_scores_a_session(session: Session):
    session.add(OiChainEod(symbol="SPX", ts=datetime.combine(MON, time()), expiry=FRI, strike=7500.0, cp="P",
                           oi=21000, iv=0.2, source="convex_am_set"))
    session.add_all([EquityBar(symbol="SPX", date=date(2026, 11, 13), close=7561.67),
                     EquityBar(symbol="SPX", date=MON, close=7416.45)])
    # 10:00 ET = 15:00 UTC on the tape (UTC until 11/19)
    session.add(TasPrint(captured_at=datetime(2026, 11, 16, 15), ts=datetime(2026, 11, 16, 15, 0, 30),
                         trade_date=MON, symbol=".SPXW", root="SPXW", spot=7480.0, source="convex_index"))
    session.commit()
    assert J.score_day(session, MON) == 9
    r = session.query(DecayLineMark).filter_by(mark="10:00", variant="N").one()
    assert r.spot == 7480.0 and r.es_at_spot < 0 and r.flip_above == pytest.approx(7500, abs=5)
    assert r.move == pytest.approx(7416.45 - 7480.0) and r.hit is True     # sell pressure, SPX fell
    assert J.score_day(session, MON) == 9 and session.query(DecayLineMark).count() == 9   # idempotent
    rep = J.report(session)
    assert rep["N"]["n"] == 1 and rep["N"]["hit_rate"] == 1.0
