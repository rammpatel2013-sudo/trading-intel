"""Tests for the intraday_flow retention prune job (SQLite)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import IntradayFlow
from trading_intel.scheduler.jobs import prune_intraday

NOW = datetime(2026, 5, 24, 12, 0)


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com", CONVEX_PASSWORD="x", FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    IntradayFlow.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add(session: Session, ts: datetime) -> None:
    session.add(
        IntradayFlow(symbol="SPX", ts=ts, source="convex",
                     expiry=ts.date(), strike=5000.0, cp="C")
    )


def _count(session: Session) -> int:
    return session.execute(select(func.count()).select_from(IntradayFlow)).scalar_one()


def test_prune_deletes_only_old_rows(session: Session):
    _add(session, NOW - timedelta(hours=49))  # stale -> delete
    _add(session, NOW - timedelta(hours=72))  # stale -> delete
    _add(session, NOW - timedelta(hours=47))  # fresh -> keep
    _add(session, NOW - timedelta(hours=1))   # fresh -> keep
    session.commit()

    deleted = prune_intraday.run(session, settings=_settings(), retention_hours=48, now=NOW)
    assert deleted == 2
    assert _count(session) == 2


def test_prune_respects_settings_default(session: Session):
    _add(session, NOW - timedelta(hours=100))
    session.commit()
    deleted = prune_intraday.run(session, settings=_settings(INTRADAY_RETENTION_HOURS=48), now=NOW)
    assert deleted == 1
    assert _count(session) == 0


def test_prune_no_rows(session: Session):
    assert prune_intraday.run(session, settings=_settings(), now=NOW) == 0
