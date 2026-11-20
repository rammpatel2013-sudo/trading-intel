"""ETF board (2026-11-19): maths (calc.py) + the build step from DB tables only (SQLite)."""

import json
import math
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.etf_board import ETF_LIST, TRADES_24H, calc
from trading_intel.mcp.etf_board_tools import etf_board
from trading_intel.memory.models_equity import EquityBar
from trading_intel.memory.models_etf import EtfBoardDaily
from trading_intel.memory.models_flow import Bars1m, FlowUndDaily
from trading_intel.scheduler.jobs import etf_board as J


def test_list_and_bsr_decoded_from_tos():
    assert len(ETF_LIST) == 48 and len(set(ETF_LIST)) == 48 and "BRK-B" in ETF_LIST
    assert TRADES_24H <= set(ETF_LIST)
    # the screenshot checks in the spec
    assert calc.bsr(4747, 407) == pytest.approx(11.66, abs=0.01)
    assert calc.bsr(197, 11453) == pytest.approx(-58.1, abs=0.05)
    assert calc.bsr(0, 5) is None and calc.bsr(5, None) is None


def test_buy_sell_split():
    m = [(10, 11, 10, 11, 100), (11, 11, 11, 11, 50), (11, 11, 11, 10.5, 10), (10, 10, 10, 10, 0)]
    b, s = calc.buy_sell(m, 10)
    # bar 1 all buy ($ 100 x 32/3); bar 2 flat & unchanged -> half; bar 3 flat, down -> sell
    assert b == pytest.approx(100 * 32 / 3 + 0.5 * 50 * 11)
    assert s == pytest.approx(0.5 * 50 * 11 + 10 * 32.5 / 3)


def test_daily_stats_and_helpers():
    c = [100 * (1.01 ** i) for i in range(60)]
    st = calc.daily_stats(c, [x * 1.01 for x in c], [x * 0.99 for x in c], [1000] * 59 + [2000])
    assert st["chg_1d"] == pytest.approx(0.01) and st["chg_5d"] == pytest.approx(1.01 ** 5 - 1)
    assert st["mo3"] == 2 and st["relvol"] == pytest.approx(2.0) and st["mo2"] == pytest.approx(0, abs=1e-9)
    assert (st["stretch"] > 0 and "strength" not in st) or math.isfinite(st["strength"])
    assert calc.corr([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1) and calc.corr([1, 2], [1, 2]) is None
    assert calc.slope([0, 1, 2, 3]) == pytest.approx(1)
    assert calc.building([1, 1, 1], 10) == pytest.approx(0.1) and calc.building([1], 10) is None


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    for m in (EquityBar, Bars1m, FlowUndDaily, EtfBoardDaily):
        m.__table__.create(eng)
    with Session(eng) as s:
        yield s


def _seed(s: Session, day: date) -> None:
    d0 = day - timedelta(days=80)
    k = 0
    for i in range(81):
        d = d0 + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        k += 1
        for sym, f in (("QQQ", 1.0), ("PSQ", -1.0), ("SPY", 0.9)):
            c = 100 * (1 + f * 0.01 * math.sin(k / 3))
            if d < day:
                s.add(EquityBar(symbol=sym, date=d, open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1000))
    for sym, up in (("PSQ", False), ("SPY", True), ("QQQ", True)):
        for j in range(390):
            ts = datetime.combine(day, datetime.min.time()) + timedelta(hours=9, minutes=30 + j)
            c = 100 + (j / 390 if up else -j / 390)
            s.add(Bars1m(symbol=sym, ts=ts, source="fmp", open=c, high=c + 0.05, low=c - 0.05,
                         close=c + (0.04 if up else -0.04), volume=100))
    s.add(FlowUndDaily(date=day, symbol="SPY", call_volume=1000.0, put_volume=2500.0, put_call_ratio=2.5,
                       volatility=0.16, front_volatility=0.17, back_volatility=0.15))
    s.commit()


def test_build_from_db_only(session, tmp_path, monkeypatch):
    day = date(2026, 11, 18)
    _seed(session, day)
    monkeypatch.setattr(J, "COCKPIT_DATA", tmp_path)
    rows = {r["symbol"]: r for r in J.build(session, day)}
    assert len(rows) == 48
    spy, psq = rows["SPY"], rows["PSQ"]
    assert spy["n_minutes"] == 390 and spy["bsr"] > 1 and psq["bsr"] < -1        # buyers vs sellers
    assert spy["corr20"] > 0.95 and psq["corr20"] < -0.95
    assert spy["pc_ratio"] == 2.5 and spy["iv_diff"] == pytest.approx(0.02) and spy["trades_24h"] is True
    assert spy["vwap"] == pytest.approx((spy["buy_usd"] + spy["sell_usd"]) / 39000)
    assert rows["UNG"]["n_minutes"] == 0 and rows["UNG"]["bsr"] is None             # nothing stored -> blanks
    assert session.execute(select(EtfBoardDaily).where(EtfBoardDaily.date == day)).scalars().all()
    j = json.loads((tmp_path / "etf_board.json").read_text())
    assert j["date"] == "2026-11-18" and len(j["rows"]) == 48
    # idempotent
    J.build(session, day)
    assert len(session.execute(select(EtfBoardDaily)).scalars().all()) == 48
    # MCP reader
    out = etf_board(session, "bsr", 3)
    assert out["rows"][0]["symbol"] in ("SPY", "QQQ") and len(out["rows"]) == 3
    assert etf_board(session, "-bsr", 1)["rows"][0]["symbol"] == "PSQ"
    assert "error" in etf_board(session, "nope")
