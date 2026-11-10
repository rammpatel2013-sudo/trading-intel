"""event_tools (2026-11-10): hedge response, close odds, event-day stats."""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.mcp import event_tools as ev
from trading_intel.memory.models import GreeksSnapshot, OiChainDaily, OiChainEod, QuoteDaily, Ticker


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in (GreeksSnapshot, OiChainEod, OiChainDaily, QuoteDaily, Ticker):
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_hedge_from_flat_curve_matches_hand_maths():
    # -1.25 $bn per 1% everywhere: a -1% move changes dealer delta by ~ +1.25bn -> they SELL ~ 1.25e9/(50*S)
    ref = list(np.linspace(7000, 8000, 101))
    es = ev.hedge_from_curve(ref, [-1.25] * 101, 7550.0, -1.0)
    assert es < 0 and abs(es + 1.25e9 / (50 * 7474.5)) / 3311 < 0.02
    assert ev.hedge_from_curve(ref, [-1.25] * 101, 7550.0, +1.0) > 0      # short gamma: buy into a rally


def test_odds_flat_vol_match_lognormal():
    ks = np.arange(6800.0, 8300.0, 5.0)
    s, sig, t = 7550.0, 0.16, 1 / 365
    p = ev.odds_from_smile(ks, np.full_like(ks, sig), s, t, [7494.0, 7616.0])
    def n(x):
        return 0.5 * math.erfc(-x / math.sqrt(2))
    def below(k):
        return n((math.log(k / s) + 0.5 * sig * sig * t) / (sig * math.sqrt(t)))
    assert abs(p[0] - below(7494)) < 0.01
    assert abs(p[2] - (1 - below(7616))) < 0.01
    assert abs(sum(p) - 1) < 1e-6


def test_event_day_rows_split_by_prior_gamma():
    q = {date(2026, 7, 14): (7000, 6950, 6990), date(2026, 7, 15): (7050, 6940, 7030),
         date(2026, 8, 12): (7100, 7080, 7095), date(2026, 8, 13): (7110, 6990, 7000)}
    g = {date(2026, 7, 14): 5.0, date(2026, 8, 12): -3.0}
    rows = ev.event_day_rows([date(2026, 7, 15), date(2026, 8, 13)], q, g)
    assert [r["gamma"] for r in rows] == ["long", "short"]
    assert rows[0]["range_pct"] == round(100 * 110 / 6990, 2)
    s = ev._summary(rows)
    assert s["short_gamma"]["n"] == 1 and s["long_gamma"]["up_days"] == 1


def _seed_book(session: Session) -> None:
    ts = datetime(2026, 11, 9, 16, 30)
    session.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=7550.0, gex_total=1.0, source="convex"))
    for k in range(7300, 7805, 5):
        for cp in ("C", "P"):
            session.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 11, 10), strike=float(k), cp=cp,
                                   dte=1, oi=500 + (4000 if cp == "P" and k < 7500 else 0), iv=0.16,
                                   source="convex_eod"))
            session.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 11, 20), strike=float(k), cp=cp,
                                   dte=11, oi=800, iv=0.17, source="convex_eod"))
    session.commit()


def test_hedge_response_and_close_odds_from_a_book(session: Session, monkeypatch):
    _seed_book(session)
    monkeypatch.setattr(ev, "live_expiry_floor", lambda d, now=None: date(2026, 11, 10))
    monkeypatch.setattr(ev.pt, "live_expiry_floor", lambda d, now=None: date(2026, 11, 10))
    h = ev.get_hedge_response(session, "SPX", as_of=date(2026, 11, 9))
    assert h["found"] and h["front_expiry"] == "2026-11-10" and len(h["rows"]) == 6
    o = ev.get_close_odds(session, "SPX", as_of=date(2026, 11, 9))
    assert o["found"] and o["expiry"] == "2026-11-10"
    assert abs(sum(b["odds_pct"] for b in o["buckets"]) - 100) < 0.5
    # 1 day at 16% vol on 7550 ~ 0.8 * 7550 * 0.16 / sqrt(365) ~ 50 pts
    assert 40 < o["straddle"] < 60


def test_event_day_stats_with_given_dates(session: Session):
    session.add(Ticker(symbol="SPX"))
    for d, (h, lo, c) in {date(2026, 7, 14): (7000, 6950, 6990), date(2026, 7, 15): (7050, 6940, 7030)}.items():
        session.add(QuoteDaily(symbol="SPX", date=d, open=c, high=h, low=lo, close=c, volume=0))
    session.add(OiChainDaily(symbol="SPX", ts=date(2026, 7, 14), net_gxoi=-2.0))
    session.commit()
    out = ev.get_event_day_stats(session, dates=[date(2026, 7, 15)])
    assert out["found"] and out["rows"][0]["gamma"] == "short"
