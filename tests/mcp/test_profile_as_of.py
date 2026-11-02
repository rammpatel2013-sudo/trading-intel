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


def test_profile_prices_same_day_expiry(monkeypatch) -> None:
    from trading_intel.memory.models import GreeksSnapshot

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    GreeksSnapshot.__table__.create(engine)
    s = Session(engine)
    ts = datetime(2026, 10, 2)
    for k, cp in ((5800.0, "C"), (5750.0, "P")):
        s.add(OiChainEod(symbol="SPX", ts=ts, expiry=date(2026, 10, 2), strike=k, cp=cp, dte=0,
                         oi=1000, iv=0.15, source="convex_am"))
    s.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=5780.0, source="convex"))
    s.commit()
    monkeypatch.setattr(pt, "live_expiry_floor", lambda d, now=None: d)  # read during that session
    out = pt.get_profile(s, "SPX", span=0.02, n_points=21, greeks=("gamma",))
    g = out["profiles"]["gamma"]
    assert list(g["by_expiry"]) == ["2026-10-02"]
    assert all(v == v for v in g["all"])  # no NaN


def _two_expiry_book() -> Session:
    from trading_intel.memory.models import GreeksSnapshot

    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    GreeksSnapshot.__table__.create(engine)
    s = Session(engine)
    ts = datetime(2026, 10, 23)  # Friday EOD book: its own expiry + Monday's
    for exp in (date(2026, 10, 23), date(2026, 10, 26)):
        for k, cp in ((7650.0, "P"), (7600.0, "P"), (7700.0, "C")):
            s.add(OiChainEod(symbol="SPX", ts=ts, expiry=exp, strike=k, cp=cp,
                             dte=(exp - ts.date()).days, oi=20000, iv=0.18, source="convex_eod"))
    s.add(GreeksSnapshot(symbol="SPX", ts=ts, spot=7623.0, source="convex"))
    s.commit()
    return s


def test_profile_drops_settled_expiry_on_a_later_read() -> None:
    """Real clock: the 10/23 book read any time after its close has no 10/23 expiry."""
    out = pt.get_profile(_two_expiry_book(), "SPX", span=0.02, n_points=11, greeks=("gamma",))
    assert list(out["profiles"]["gamma"]["by_expiry"]) == ["2026-10-26"]


def test_expired_rolloff_reports_the_share_that_settled() -> None:
    r = pt.expired_rolloff(_two_expiry_book(), "SPX", today=date(2026, 10, 26))
    assert r["book_day"] == "2026-10-23"
    assert r["gamma_expired"] < 0 and r["gamma_total"] < r["gamma_expired"]  # both short; total bigger
    assert abs(r["gamma_after"] - (r["gamma_total"] - r["gamma_expired"])) < 1e-9
    assert 0 < r["share"] < 1
    # the same book read through get_profile (expired dropped) equals what's left
    left = pt.get_profile(_two_expiry_book(), "SPX", span=0.001, n_points=3, greeks=("gamma",))
    assert abs(left["profiles"]["gamma"]["all"][1] - r["gamma_after"]) < 1e-6


def test_expired_rolloff_none_without_a_prior_book() -> None:
    assert pt.expired_rolloff(_two_expiry_book(), "SPX", today=date(2026, 10, 23)) is None
