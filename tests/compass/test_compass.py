"""Compass (Macro view, 2026-11-20): the maths, the scanners, and one job run on SQLite into the derived tables."""

import json
import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel import compass as C
from trading_intel.chain_bank import BANK_SOURCE
from trading_intel.memory.models import Base, GreeksChain, GreeksSnapshot, OiChainDaily
from trading_intel.memory.models_compass import AuctionDaily, CompassDaily
from trading_intel.memory.models_desk import FinraShortVolume
from trading_intel.memory.models_equity import EquityBar, EquityMember
from trading_intel.memory.models_flow import Bars1m
from trading_intel.scheduler.jobs import compass as J

DAY = date(2026, 11, 19)


def test_hv_bb_sigma_rank():
    c = 100 * np.exp(np.cumsum(np.r_[0, np.full(40, 0.01) * np.resize([1, -1], 40)]))
    assert C.hv(c, 10) == pytest.approx(0.01 * math.sqrt(252) * math.sqrt(10 / 9), rel=0.02)
    assert C.bollinger_pctb([1.0] * 19 + [1.0]) is None and 0 <= C.bollinger_pctb(list(range(1, 21))) <= 1.2
    assert C.sigma_move(-0.02, 0.16) == pytest.approx(-0.02 / (0.16 / math.sqrt(252)))
    assert C.rank(list(range(10)), 5) is None and C.rank(list(range(100)), 49) == 0.5
    s = pd.Series([0.5, 0.52, 0.18, 0.51, 0.62, 0.55], index=range(6))
    assert 2 not in C.clean_iv(s).index and len(C.clean_iv(s)) == 5


def test_dark_stats():
    r, z = C.dark_stats([40] * 20 + [70], [100] * 21)
    assert r == 0.7 and z is None                       # flat history -> no z
    r, z = C.dark_stats([40, 42, 38, 41, 39] * 4 + [60], [100] * 21)
    assert r == 0.6 and z > 5


def _session(day, path, vol=None):
    ts = pd.date_range(datetime.combine(day, time(9, 30)), periods=390, freq="min")
    px = np.asarray(path, float)
    return pd.DataFrame({"ts": ts, "open": px, "high": px + 0.05, "low": px - 0.05, "close": px,
                         "volume": vol if vol is not None else np.full(390, 1000.0)})


def test_auction_day_types():
    up = _session(DAY, np.r_[np.full(60, 100.0) + np.resize([0, .2], 60), np.linspace(100.2, 104, 330)])
    a = C.auction(up)
    assert a["day_type"] == "trend" and a["close_vs_value"] in ("above", "in") and a["ib_high"] < a["high"]
    flat = _session(DAY, 100 + 0.5 * np.sin(np.arange(390) / 10))
    b = C.auction(flat, prior={"val": 100.6, "vah": 101.0})
    assert b["day_type"] == "normal" and b["open_vs_value"] == "below" and b["poor_high"] and b["poor_low"]
    assert b["val"] <= b["poc"] <= b["vah"]
    rows = [{"poc": 100.0, "low": 99, "high": 101, "val": 99.5, "vah": 100.5},
            {"poc": 103.0, "low": 102, "high": 104, "val": 102.5, "vah": 103.5},
            {"poc": 103.2, "low": 102.5, "high": 103.8, "val": 102.8, "vah": 103.4}]
    assert C.naked_pocs(rows) == [100.0]
    assert not C.balance(rows) and C.balance(rows[1:] + [{"val": 103.0, "vah": 103.3}])


def test_flags():
    f = C.flags({"sigma_move": -2.4, "iv_rank": 0.95, "iv30": 0.4, "carry": 0.2, "ts_30_90": 1.1, "close": 100,
                 "gex": -1, "gex_flip": 101, "call_wall": 100.5, "dark_ratio": 0.6, "dark_z": 1.5, "ret5": -0.05,
                 "open_vs_value": "below", "close_vs_value": "below", "prem_usd": 5e6, "call_share": 0.2,
                 "vwap": 101, "naked_pocs": [99.5]})
    for k in ("big_move", "iv_high", "carry_rich", "term_inverted", "neg_gamma_flip", "near_wall", "dark_high_falling",
              "outside_value", "put_tilt", "naked_poc"):
        assert k in f, k
    assert "accumulation" not in f and all(k in C.SCANNERS for k in f)
    assert C.flags({}) == []


@pytest.fixture
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    T = Base.metadata.tables
    Base.metadata.create_all(eng, tables=[T[t] for t in ("equity_bars", "equity_members", "greeks_snapshots",
                                                         "greeks_chain", "oi_chain_daily", "finra_short_volume",
                                                         "bars_1m", "auction_daily", "compass_daily")])
    with Session(eng) as s:
        yield s


def test_job_build(db, tmp_path):
    days = [d for d in (DAY - timedelta(days=k) for k in range(200, -1, -1)) if d.weekday() < 5]
    rng = np.random.default_rng(1)
    for sym, start in (("SPY", 600.0), ("NVDA", 150.0), ("TLT", 90.0)):
        px = start * np.exp(np.cumsum(rng.normal(0, 0.012, len(days))))
        px[-1] = px[-2] * 0.97                                   # -3% on the day
        for d, p in zip(days, px):
            db.add(EquityBar(symbol=sym, date=d, open=p, high=p * 1.005, low=p * 0.995, close=float(p), volume=1_000_000))
    db.add(EquityMember(symbol="NVDA", index_code="SP500", start_date=date(2020, 1, 1), end_date=None, source="t"))
    for i, d in enumerate(days[-60:]):
        db.add(OiChainDaily(symbol="NVDA", ts=d, source="convex_eod", spot=150, atm_iv_30d=0.40 + 0.002 * i,
                            call_wall=200.0, put_wall=100.0))
        db.add(FinraShortVolume(date=d, symbol="NVDA", short_volume=40 + (i % 3) + (30 if d == DAY else 0), total_volume=100))
    snap_ts = datetime.combine(DAY, time(16, 5))
    db.add(GreeksSnapshot(symbol="SPY", ts=snap_ts, spot=600, iv30=0.18, iv90=0.17, ts_30_90=1.06, skew25_30=0.05,
                          gex_total=-5.0, gex_flip=float(db.scalar(select(EquityBar.close).where(EquityBar.symbol == "SPY", EquityBar.date == DAY))) * 1.01,
                          call_premium_usd=1e6, put_premium_usd=9e6, source=BANK_SOURCE))
    db.add(GreeksChain(symbol="SPY", ts=snap_ts, expiry=DAY + timedelta(days=8), strike=600, cp="C", oi=10, gxoi=50,
                       iv=0.18, spot=600, source=BANK_SOURCE))
    for m in range(390):
        t = datetime.combine(DAY, time(9, 30)) + timedelta(minutes=m)
        db.add(Bars1m(symbol="SPY", ts=t, source="fmp", open=600, high=600.2, low=599.8, close=600 - m * 0.01, volume=1000))
    db.commit()
    out = J.run(db, day=DAY, steps=("auction", "build"), out_dir=tmp_path)
    assert not out["errors"] and out["auction"]["names"] == 1 and out["build"]["rows"] == 3
    j = json.loads((tmp_path / "compass.json").read_text())
    nv, spy = j["names"]["NVDA"], j["names"]["SPY"]
    assert nv["iv_src"] == "convex_eod" and nv["iv_rank"] == 1.0 and "iv_high" in nv["flags"]
    assert nv["sigma_move"] == pytest.approx(nv["ret1"] / (0.518 / math.sqrt(252)), rel=0.01)
    assert nv["dark_ratio"] == pytest.approx(0.71, abs=0.02) and "dark_spike" in nv["flags"]
    assert spy["iv_src"] == "hv20" and "term_inverted" in spy["flags"] and "neg_gamma_flip" in spy["flags"]
    assert "put_tilt" in spy["flags"] and spy["poc"] is not None and spy["call_wall"] == 600
    p_spy = json.loads((tmp_path / "compass_p" / "SPY.json").read_text())
    assert j["groups"]["Indices"][0] == "SPY" and len(p_spy["d"]) == 120 and p_spy["date"] == "2026-11-19"
    assert p_spy["gamma"]["k"] == [600.0]
    assert sorted(f.stem for f in (tmp_path / "compass_p").glob("*.json")) == ["NVDA", "SPY", "TLT"]   # every name
    sc = {s["id"]: s for s in j["scanners"]}
    assert "NVDA" in sc["iv_high"]["hits"] and sc["iv_high"]["meaning"]
    assert db.scalar(select(func.count()).select_from(CompassDaily)) == 3
    assert db.scalar(select(func.count()).select_from(AuctionDaily)) == 1
    J.run(db, day=DAY, steps=("build",), out_dir=tmp_path)                         # re-run: upsert, no duplicates
    assert db.scalar(select(func.count()).select_from(CompassDaily)) == 3
    assert J.run(db, day=date(2026, 11, 21), out_dir=tmp_path) == {"skipped": "not_a_trading_session"}
