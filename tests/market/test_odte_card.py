"""odte_card (2026-11-14): the AM card from ONE book centred on its own 08:35 forward, plus the overnight
change. Books here are priced like the real 11/13 one: prior close 7,592, futures up overnight -> 7,645,
vendor IVs wrong on purpose."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market import clean_book as cb
from trading_intel.market import odte_card as oc
from trading_intel.memory.models import GreeksSnapshot, IvTenorSnapshot, OiChainEod


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in (GreeksSnapshot, OiChainEod, IvTenorSnapshot):
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _book(s: Session, day: date, fwd: float, atm: float, oi_shift: float = 0.0) -> None:
    book = datetime.combine(day, cb.AM_BOOK_TIME)
    for exp in (day, date(2026, 11, 16), date(2026, 11, 20), date(2026, 12, 18)):
        if exp < day:
            continue
        t = cb.years(book, cb.settle_dt(exp, False))
        for k in np.arange(7300.0, 8000.0, 5.0):
            iv = atm - 0.5 * np.log(k / fwd)
            for cp in ("C", "P"):
                m = float(cb.b76(fwd, np.array([k]), np.array([iv]), t, np.array([cp == "C"]))[0])
                oi = 300.0 + oi_shift
                if cp == "P" and k == 7500:
                    oi = 50000.0
                if cp == "C" and k == 7700:
                    oi = 40000.0
                if k == 7600:
                    oi = 20000.0 + oi_shift
                if cp == "P" and k < 7450:
                    oi = 2500.0
                bid, ask = (round(m - 0.05, 2), round(m + 0.05, 2)) if m > 0.1 else (None, None)
                s.add(OiChainEod(symbol="SPX", ts=datetime.combine(day, datetime.min.time()), expiry=exp,
                                 strike=float(k), cp=cp, dte=(exp - day).days, oi=oi, iv=0.0023,
                                 bid_price=bid, ask_price=ask, source="convex_am"))


def _seed(s: Session, today_atm: float = 0.144) -> None:
    _book(s, date(2026, 11, 12), 7569.6, 0.155)
    _book(s, date(2026, 11, 13), 7645.0, today_atm, oi_shift=900.0)
    for when, spot in ((datetime(2026, 11, 11, 18, 10), 7617.84), (datetime(2026, 11, 12, 18, 10), 7592.06),
                       (datetime(2026, 11, 13, 18, 10), 7560.97)):
        s.add(GreeksSnapshot(symbol="SPX", ts=when, spot=spot, gex_total=0.0, source="convex"))
    s.add(IvTenorSnapshot(symbol="SPX", ts=date(2026, 11, 12), tenor_dte=30, iv_atm=0.1404))
    s.commit()


def _card(s: Session, monkeypatch, floor: date = date(2026, 11, 13), **kw) -> dict:
    monkeypatch.setattr(oc, "live_expiry_floor", lambda d, now=None: floor)
    return oc.build_odte_card(s, "SPX", as_of=date(2026, 11, 13), **kw)


def test_am_card_is_centred_on_the_0835_forward_not_a_close(session: Session, monkeypatch):
    _seed(session)
    c = _card(session, monkeypatch)
    assert c["found"] and abs(c["centre"] - 7645.0) < 0.3 and c["prior_close"] == 7592.06
    assert c["session"] == "2026-11-13" and c["checks"]["front_is_today"] and c["book_time"] == "2026-11-13T08:35"
    assert c["latest"]["spot"] == 7560.97 and c["latest"]["after_book"]
    assert c["odds_quality"] == "smile" and c["odds_reasons"] == []
    assert c["levels"]["floor"]["strike"] == 7500.0 and c["levels"]["cap"]["strike"] == 7700.0
    tw = c["three_ways"]
    assert abs(tw["break"] + tw["range"] + tw["pop"] - 100) < 0.5
    assert "parity" in c["sources"]["forward"] and "dealers long the calls" in c["dealer_sign"]


def test_overnight_gap_vol_and_positions(session: Session, monkeypatch):
    _seed(session)
    on = _card(session, monkeypatch)["overnight"]
    assert abs(on["gap_pts"] - 52.9) < 0.5 and on["prev_book_day"] == "2026-11-12"
    v = on["vol"]["7d"]
    assert v["change"] < 0 and v["flag"] == "normal"                          # 15.5% -> 14.4%
    assert on["oi"]["updated"] and on["oi"]["changed_pct"] > 80
    assert on["vol_since_close"]["close"] == 14.04


def test_overnight_vol_spike_is_flagged(session: Session, monkeypatch):
    _seed(session, today_atm=0.22)                                           # +6.5 vol pts overnight
    v = _card(session, monkeypatch)["overnight"]["vol"]["7d"]
    assert v["flag"] == "spike" and v["change"] > 5


def test_weekend_read_says_it_is_not_the_monday_book(session: Session, monkeypatch):
    _seed(session)
    c = _card(session, monkeypatch, floor=date(2026, 11, 14))
    assert c["session"] == "2026-11-16" and not c["checks"]["front_is_today"]


def test_live_refresh_uses_live_prices_with_the_morning_oi(session: Session, monkeypatch):
    _seed(session)
    live_dt = datetime(2026, 11, 13, 9, 45)
    rows = []
    t = cb.years(live_dt, cb.settle_dt(date(2026, 11, 13), False))
    for k in np.arange(7300.0, 8000.0, 5.0):
        for cp in ("C", "P"):
            m = float(cb.b76(7617.0, np.array([k]), np.array([0.20]), t, np.array([cp == "C"]))[0])
            if m > 0.1:
                rows.append({"expiry": date(2026, 11, 13), "strike": float(k), "cp": cp, "oi": 1000,
                             "bid": m - 0.05, "ask": m + 0.05, "source": "convex_live", "iv": None})
    c = _card(session, monkeypatch, live_rows=rows, live_dt=live_dt)
    assert c["found"] and abs(c["centre"] - 7617.0) < 0.3 and c["label"] == "LIVE 09:45"


def test_pick_levels_finds_air_pocket():
    oi = {7500.0: {"C": 0, "P": 50000}, 7505.0: {"C": 100, "P": 100}, 7600.0: {"C": 20000, "P": 20000},
          7700.0: {"C": 40000, "P": 0}}
    lv = oc.pick_levels(oi, 7561.0, 220.0, 44.0)
    assert lv["floor"]["strike"] == 7500.0 and lv["cap"]["strike"] == 7700.0
    assert [7500.0, 7600.0] in lv["air_pockets"] and [7600.0, 7700.0] in lv["air_pockets"]


def test_vol_change_ranks_against_history():
    hist = [(date(2026, 11, 12), 0.155, 0.146), (date(2026, 11, 11), 0.145, 0.145),
            (date(2026, 11, 10), 0.164, 0.160), (date(2026, 11, 9), 0.202, 0.179)]
    out = oc.vol_change((0.180, 0.150), hist)
    assert out["7d"]["flag"] == "spike" and out["7d"]["change"] == 2.5 and out["7d"]["pct_rank"] == 100


def test_regime_words():
    assert oc.regime(7561, 7601, 7643)["key"] == "neg"
    assert oc.regime(7700, 7601, 7643)["key"] == "pos"
    assert oc.regime(7620, 7601, 7643)["key"] == "mid"
