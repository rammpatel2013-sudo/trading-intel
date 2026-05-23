"""Tests for the options-flow collector — SQLite, fake source."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import FlowSnapshot
from trading_intel.scheduler.jobs import flow_snapshot


class FakeSource:
    def flow_chain(self, symbol, **_):
        return pd.DataFrame(
            [
                {"opt_kind": "call", "expiration": pd.Timestamp("2026-05-22"), "strike": 100.0,
                 "premium": 3_000_000.0, "iv": 0.22},
                {"opt_kind": "put", "expiration": pd.Timestamp("2026-05-22"), "strike": 95.0,
                 "premium": 1_000_000.0, "iv": 0.30},
            ]
        )

    def time_and_sales(self, symbol, **_):
        # Two legs printed on the same ticket (root+time) -> a call spread package.
        t = pd.Timestamp("2026-05-22 10:00:00")
        return pd.DataFrame(
            [
                {"time": t, "root": symbol, "expiration": pd.Timestamp("2026-05-22"),
                 "strike": 100.0, "opt_kind": "call", "size": 500, "premium": 600_000.0,
                 "aggressor_side": "buy"},
                {"time": t, "root": symbol, "expiration": pd.Timestamp("2026-05-22"),
                 "strike": 105.0, "opt_kind": "call", "size": 500, "premium": 200_000.0,
                 "aggressor_side": "sell"},
            ]
        )


class EmptyFlowSource(FakeSource):
    def flow_chain(self, symbol, **_):
        return pd.DataFrame()


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    FlowSnapshot.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(FlowSnapshot)).scalar_one()


def test_run_writes_aggregate_and_payloads(session: Session):
    flow_snapshot.run(session, FakeSource(), settings=_settings(), force=True)
    row = session.execute(select(FlowSnapshot)).scalar_one()
    assert row.call_notional == pytest.approx(3_000_000.0)
    assert row.put_notional == pytest.approx(1_000_000.0)
    assert row.put_call_ratio == pytest.approx(1_000_000.0 / 3_000_000.0)
    assert row.tilt == "offensive (call-heavy)"
    assert row.n_prints == 2
    # top_prints JSON is sanitized (expiration as string).
    assert row.top_prints[0]["expiration"] == "2026-05-22"
    # One multi-leg package detected ($800K total >= 250K min).
    assert len(row.packages) == 1
    assert row.packages[0]["n_legs"] == 2
    assert "call spread" in row.packages[0]["kind"]


def test_run_idempotent_same_minute(session: Session):
    s = _settings()
    flow_snapshot.run(session, FakeSource(), settings=s, force=True)
    n1 = _count(session)
    flow_snapshot.run(session, FakeSource(), settings=s, force=True)
    assert _count(session) == n1


def test_run_skips_symbol_with_no_flow(session: Session):
    flow_snapshot.run(session, EmptyFlowSource(), settings=_settings(), force=True)
    assert _count(session) == 0


def test_run_off_hours_guard(session: Session, monkeypatch):
    monkeypatch.setattr(flow_snapshot, "is_market_hours", lambda _n: False)
    flow_snapshot.run(session, FakeSource(), settings=_settings(), force=False)
    assert _count(session) == 0
