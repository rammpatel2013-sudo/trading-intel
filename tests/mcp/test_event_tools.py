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


# ---------------------------------------------------------------- 2026-11-12 close-odds repair
def test_pava_down_never_rises_and_keeps_good_series():
    y = np.array([1.0, 0.9, 0.95, 0.5, 0.6, 0.0])
    f = ev.pava_down(y)
    assert np.all(np.diff(f) <= 1e-12)
    assert np.allclose(ev.pava_down(np.array([1.0, 0.7, 0.2])), [1.0, 0.7, 0.2])
    assert abs(f.sum() - y.sum()) < 1e-9          # least squares keeps the mean of each pooled block


def _kinked_rows(spot: float) -> list[tuple]:
    rows = []
    for k in range(7300, 7905, 5):
        put_iv = 0.18 + 0.9 * max(0.0, (spot - k) / spot)       # steep put skew
        call_iv = 0.12                                          # calls much lower: a step at spot
        if k == 7480:
            put_iv = 0.95                                       # one bad print
        rows.append((float(k), "P", put_iv, 1.0, 1.2))
        rows.append((float(k), "C", call_iv, 1.0, 1.2))
        if k == 7700:
            rows.append((float(k), "C", 0.11, 0.0, 0.05))       # one-sided quote: dropped
    return rows


def test_clean_smile_blends_at_spot_and_drops_bad_rows():
    spot = 7594.68
    ks, vs, dropped = ev.clean_smile(_kinked_rows(spot), spot)
    assert dropped["one_sided"] == 1 and dropped["outlier"] >= 1
    assert 7480.0 not in set(ks.tolist())
    # inside 0.5% of spot the two sides are averaged, so no single jump of 6 vol points remains
    assert np.max(np.abs(np.diff(vs))) < 0.06


def test_kinked_smile_odds_have_no_hole_near_spot():
    spot, t = 7594.68, 6.5 / 8760
    ks, vs, _ = ev.clean_smile(_kinked_rows(spot), spot)
    grid, surv = ev.survival_from_smile(ks, vs, spot, t)
    edges = list(range(7560, 7640, 10))
    p = ev._buckets(grid, surv, edges)
    assert all(x > 0.01 for x in p[1:-1])                    # every 10-pt bucket near spot carries odds
    st = ev.dist_stats(grid, surv, spot, 40.0)
    assert abs(st["median"] - spot) < 40 and st["max_5pt"] < 0.25
    assert ev.odds_quality(st, spot, 40.0) == []


def test_quality_check_flags_collapsed_odds():
    grid = np.linspace(7400, 7800, 801)
    surv = np.where(grid < 7575, 1.0, np.where(grid < 7580, 0.2, 0.0))   # 80% in one 5-pt bucket
    st = ev.dist_stats(grid, surv, 7594.68, 40.0)
    why = ev.odds_quality(st, 7594.68, 40.0)
    assert any("one 5-point bucket" in w for w in why)


def test_close_odds_reports_book_time_quality_and_stats(session: Session, monkeypatch):
    _seed_book(session)
    monkeypatch.setattr(ev, "live_expiry_floor", lambda d, now=None: date(2026, 11, 10))
    monkeypatch.setattr(ev.pt, "live_expiry_floor", lambda d, now=None: date(2026, 11, 10))
    o = ev.get_close_odds(session, "SPX", as_of=date(2026, 11, 9))
    assert o["found"] and o["quality"] == "smile" and o["quality_reasons"] == []
    assert o["book_ts"] == "2026-11-09T16:30" and abs(o["t_hours"] - 24.0) < 0.01
    assert o["p10"] < o["median"] < o["p90"] and abs(o["median"] - 7550) < 30
    assert "re-priced" in o["gamma_flip_def"]


def test_hedge_response_ex_monthly_column(session: Session, monkeypatch):
    _seed_book(session)                                       # 11/20/2026 = 3rd Friday (monthly)
    ts = datetime(2026, 11, 9, 16, 30)
    for k in range(7300, 7805, 5):                            # a put-heavy December monthly (12/18)
        for cp in ("C", "P"):
            session.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 12, 18), strike=float(k), cp=cp,
                                   dte=39, oi=300 + (6000 if cp == "P" else 0), iv=0.18, source="convex_eod"))
    session.commit()
    monkeypatch.setattr(ev.pt, "live_expiry_floor", lambda d, now=None: date(2026, 11, 10))
    h = ev.get_hedge_response(session, "SPX", as_of=date(2026, 11, 9))
    assert h["monthly_quarterly_expiries"] == ["2026-11-20", "2026-12-18"]
    assert all("es_k_ex_monthly" in r for r in h["rows"])
    assert any(r["es_k_ex_monthly"] != r["es_k"] for r in h["rows"])
    assert ev._is_monthly_or_quarterly("2026-12-31") and not ev._is_monthly_or_quarterly("2026-11-13")
