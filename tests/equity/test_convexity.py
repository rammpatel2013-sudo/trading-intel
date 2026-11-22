"""Convexity scanner (2026-11-22): the four lists, filters, paper-track scoring, ETF vol board, job on SQLite."""

import json
from datetime import date, timedelta

import numpy as np
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.equity import convexity as cx
from trading_intel.equity.stage_groups import daily_weekly

D = date(2026, 11, 20)


def row(sym="AAA", **kw):
    r = {"symbol": sym, "grp": "stock", "iv_src": "convex_eod", "close": 100.0, "iv30": 0.30, "iv_rank": 0.5,
         "carry": 0.0, "ts_30_90": 0.95, "skew_rank": 0.5, "hv10": 0.30, "prem_usd": 2e6, "ret20": 0.05,
         "code": "2B", "wstage": 2, "mrs": 3.0}
    r.update(kw)
    return r


def test_buckets():
    assert cx.classify(row(iv_rank=0.2, carry=-0.01))[0] == "cheap_up"
    assert cx.classify(row(iv_rank=None, carry=-0.01))[0] == "cheap_up"            # no rank yet: IV <= HV10
    assert cx.classify(row(iv_rank=0.2, carry=-0.01, skew_rank=0.1)) is None       # upside skew already bid
    assert cx.classify(row(code="4A", wstage=4, mrs=-5, iv_rank=0.3, carry=0.0))[0] == "cheap_down"
    assert cx.classify(row(iv_rank=0.9, carry=0.12, iv30=0.35))[0] == "rich_sell"
    assert cx.classify(row(iv_rank=0.9, carry=0.12, code="3A")) is None            # daily topping: not a sale
    assert cx.classify(row(iv_rank=0.9, carry=0.12, ts_30_90=1.1)) is None         # inverted term: not a sale
    assert cx.classify(row(code="4B", wstage=4, mrs=-4, carry=-0.08, ts_30_90=1.10))[0] == "avoid"
    assert cx.classify(row(iv_src="hv20", iv_rank=0.1, carry=-0.05)) is None       # no real option IV


def test_pick_filters_and_ranks():
    rows = [row("A", iv_rank=0.1, carry=-0.02, mrs=8), row("B", iv_rank=0.25, carry=0.0, mrs=1),
            row("C", iv_rank=0.1, carry=-0.02, prem_usd=1e4),                  # too thin
            row("E", iv_rank=0.1, carry=-0.02),                                # reports in 10 days
            row("F", iv_rank=0.1, carry=-0.02, iv_src="hv20"),
            row("SPY", grp="Indices", iv_rank=0.1, carry=-0.02)]               # grid ETF: board only
    picks, cnt = cx.pick(rows, D, {"E": D + timedelta(days=10)})
    assert [p["symbol"] for p in picks] == ["A", "B"] and picks[0]["rank"] == 1
    assert cnt["skipped"] == {"earnings": 1, "illiquid": 1, "no_real_iv": 1} and cnt["cheap_up"] == 2
    assert picks[0]["move20"] == pytest.approx(0.30 * (20 / 252) ** 0.5)


def test_score_each_bucket():
    sig5 = 0.30 * (5 / 252) ** 0.5
    up = [100, 101, 102, 103, 100 * (1 + sig5) + 0.1]
    p = {"bucket": "cheap_up", "spot": 100.0, "iv30": 0.30}
    assert cx.score(p, up, up, up, 5)["hit"] is True
    assert cx.score({**p, "bucket": "cheap_down"}, up, up, up, 5)["hit"] is False
    assert cx.score({**p, "bucket": "rich_sell"}, up, up, up, 5)["hit"] is True
    assert cx.score({**p, "bucket": "avoid"}, up, up, up, 5)["hit"] is True
    assert cx.score(p, up[:3], up[:3], up[:3], 5) is None                      # not enough bars yet
    assert cx.score(p, up, up, up, 5)["rv_iv"] > 0


def test_track_aggregates():
    days = [D + timedelta(days=i) for i in range(1, 30)]
    bars = {"A": [(d, 100.0 + 2 * i, None, None) for i, d in enumerate(days)]}
    t = cx.track([{"date": D, "symbol": "A", "bucket": "cheap_up", "spot": 100.0, "iv30": 0.2}], bars)
    r = {(x["bucket"], x["h"]): x for x in t["rows"]}
    assert r[("cheap_up", 5)]["n"] == 1 and r[("cheap_up", 5)]["hit"] == 1.0 and r[("rich_sell", 5)]["n"] == 0
    assert t["dates_scored"] == 1


def test_vol_board():
    b = cx.vol_board([row("TLT", grp="Bonds", iv30=0.15, hv30=0.10, iv_rank=0.8),
                      row("GLD", grp="Commodities", iv30=0.12, hv30=0.20, iv_rank=0.2), row("AAA")])
    q = {x["symbol"]: x["quad"] for x in b}
    assert q == {"TLT": "sell premium", "GLD": "buy options"}


def test_daily_weekly_no_peeking():
    dates = [date(2025, 1, 6) + timedelta(days=i) for i in range(500) if (date(2025, 1, 6) + timedelta(days=i)).weekday() < 5]
    close = np.linspace(50, 150, len(dates))[:, None] * np.ones((1, 2))
    spy = np.linspace(100, 110, len(dates))
    st, rs = daily_weekly(dates, close, spy)
    assert st[-1, 0] == 2 and rs[-1, 0] > 0 and st[0, 0] == 0
    mon = next(i for i in range(len(dates) - 10, len(dates)) if dates[i].weekday() == 0)
    assert st[mon, 0] == st[mon - 1, 0]                                       # Monday uses last Friday's week


def test_job_on_sqlite(tmp_path, monkeypatch):
    from trading_intel.memory.models import EarningsEvent
    from trading_intel.memory.models_compass import CompassDaily
    from trading_intel.memory.models_equity import EquityBar, EquityConvexPick, EquityStage
    from trading_intel.scheduler.jobs import convexity as job

    eng = create_engine("sqlite://")
    for m in (CompassDaily, EquityStage, EquityConvexPick, EquityBar, EarningsEvent):
        m.__table__.create(eng)
    with Session(eng) as s:
        past = D - timedelta(days=14)
        for d, px in ((past, 100.0), (D, 100.0)):
            s.add(CompassDaily(symbol="A", date=d, grp="stock", iv_src="convex_eod", close=px, iv30=0.3, iv_rank=0.1,
                               carry=-0.02, ts_30_90=0.9, skew_rank=0.5, hv10=0.32, prem_usd=1e6, ret20=0.04))
            s.add(EquityStage(date=d, symbol="A", code="2B", stage=2, xatr=1.0, wstage=2, mrs=4.0))
        s.add(CompassDaily(symbol="TLT", date=D, grp="Bonds", iv_src="convex_eod", close=90, iv30=0.14, hv30=0.1,
                           iv_rank=0.7, carry=0.03, ts_30_90=0.98))
        for i in range(1, 15):
            s.add(EquityBar(symbol="A", date=past + timedelta(days=i), close=100.0 + i, high=101.0 + i, low=99.0 + i))
        s.commit()
        assert job.run(s, day=past, force=True, out_dir=tmp_path)["counts"]["cheap_up"] == 1
        out = job.run(s, day=D, force=True, out_dir=tmp_path)
        again = job.run(s, day=D, force=True, out_dir=tmp_path)
        assert out["rows"] == again["rows"] == 1
        assert s.execute(select(func.count()).select_from(EquityConvexPick)).scalar() == 2   # idempotent per day
        o = json.loads((tmp_path / "convexity.json").read_text(encoding="utf-8"))
        assert o["picks"]["cheap_up"][0]["symbol"] == "A" and o["board"][0]["symbol"] == "TLT"
        r5 = next(r for r in o["track"]["rows"] if r["bucket"] == "cheap_up" and r["h"] == 5)
        assert r5["n"] == 1 and r5["hit"] == 1.0
