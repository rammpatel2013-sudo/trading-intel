"""Tests for the intraday 0DTE/1DTE flow collector — SQLite, fake source."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import IntradayFlow
from trading_intel.scheduler.jobs import intraday_flow


class FakeSource:
    """Minimal OptionsDataSource: returns a canned chain + spot, volume rises."""

    def __init__(self) -> None:
        self._volume = 1000

    def chain(self, symbol: str, *, exps=(1, 2, 3), strike_range: float = 0.15) -> pd.DataFrame:
        today = date.today()
        return pd.DataFrame(
            [
                {"expiration": pd.Timestamp(today), "opt_kind": "call", "strike": 100.0,
                 "gamma": 0.05, "delta": 0.5, "vanna": 0.1, "charm": 0.02, "iv": 0.20,
                 "volume": self._volume, "oi": 10},
                {"expiration": pd.Timestamp(today), "opt_kind": "put", "strike": 100.0,
                 "gamma": 0.04, "delta": -0.5, "vanna": 0.1, "charm": 0.02, "iv": 0.20,
                 "volume": self._volume // 2, "oi": 10},
            ]
        )

    def spot(self, symbol: str) -> float:
        return 100.0


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    IntradayFlow.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _settings() -> Settings:
    return Settings(
        CONVEX_EMAIL="x@example.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://example.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        INTRADAY_SYMBOLS="SPX",
    )


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(IntradayFlow)).scalar_one()


def test_run_writes_per_strike_rows(session: Session):
    intraday_flow.run(session, FakeSource(), settings=_settings(), force=True)
    rows = list(session.execute(select(IntradayFlow)).scalars())
    assert len(rows) == 2  # one call + one put at strike 100
    by_cp = {r.cp: r for r in rows}
    # gamma_vol is the per-strike aggregate stored on each row at that strike:
    # +0.05*1000 (call) - 0.04*500 (put) = 30. First slot has no prior -> interval NULL.
    assert by_cp["C"].gamma_vol == pytest.approx(30.0)
    assert by_cp["P"].gamma_vol == pytest.approx(30.0)
    assert by_cp["C"].volume == 1000
    assert by_cp["C"].volume_interval is None  # raw fresh-flow unknown on first slot
    assert by_cp["C"].gamma_vol_iv == pytest.approx(0.0)  # weighted fresh flow = 0


def test_run_is_idempotent_same_slot(session: Session):
    s = _settings()
    src = FakeSource()
    intraday_flow.run(session, src, settings=s, force=True)
    n1 = _count(session)
    # Re-run within the same 5-min slot -> ON CONFLICT DO NOTHING, no growth.
    intraday_flow.run(session, src, settings=s, force=True)
    assert _count(session) == n1


def test_run_off_hours_guard_skips(session: Session, monkeypatch):
    monkeypatch.setattr(intraday_flow, "is_market_hours", lambda _now: False)
    intraday_flow.run(session, FakeSource(), settings=_settings(), force=False)
    assert _count(session) == 0


def test_interval_path_across_two_slots(session: Session, monkeypatch):
    s = _settings()
    src = FakeSource()
    slots = iter([datetime(2026, 5, 22, 10, 0), datetime(2026, 5, 22, 10, 5)])
    monkeypatch.setattr(intraday_flow, "_floor_to_slot", lambda now, minutes=5: next(slots))

    intraday_flow.run(session, src, settings=s, force=True)  # slot 1: vol 1000/500
    src._volume = 1300  # call 1300, put 650 in slot 2
    intraday_flow.run(session, src, settings=s, force=True)  # slot 2

    later = max(r.ts for r in session.execute(select(IntradayFlow)).scalars())
    rows = list(
        session.execute(select(IntradayFlow).where(IntradayFlow.ts == later)).scalars()
    )
    by_cp = {r.cp: r for r in rows}
    # call fresh volume = 1300 - 1000 = 300; put = 650 - 500 = 150
    assert by_cp["C"].volume_interval == 300
    assert by_cp["P"].volume_interval == 150
    # per-strike interval gamma_vol_iv = +0.05*300 (call) - 0.04*150 (put) = 15 - 6 = 9
    assert by_cp["C"].gamma_vol_iv == pytest.approx(9.0)
