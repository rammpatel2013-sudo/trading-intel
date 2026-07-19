"""Unit tests for the EOD tas_daily_rollup job (in-memory SQLite).

Creates the raw + roll-up tables, seeds a session of tas_prints, rolls it up, and
checks the per-name / per-contract aggregates, accumulation direction, and
idempotency (the ON CONFLICT DO UPDATE refresh).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import TasDailyContract, TasDailyFlow, TasPrint
from trading_intel.scheduler.jobs import tas_daily_rollup

_DAY = date(2026, 6, 24)


def _settings(**kw) -> Settings:
    base = dict(
        CONVEX_EMAIL="x@e.com",
        CONVEX_PASSWORD="x",
        FRED_API_KEY="x",
        DISCORD_WEBHOOK_URL="https://e.com/h",
        DATABASE_URL="postgresql+psycopg://intel:intel@localhost:5432/trading_intel",
        WATCHLIST="SPY,QQQ,NVDA",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for model in (TasPrint, TasDailyFlow, TasDailyContract):
        model.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _print(**kw) -> TasPrint:
    base = dict(
        captured_at=datetime(2026, 6, 24, 12, 0),
        ts=datetime(2026, 6, 24, 13, 30),
        trade_date=_DAY,
        symbol=".NVDA260717C210",
        root="NVDA",
        expiry=date(2026, 7, 17),
        strike=210.0,
        cp="C",
        side="buy",
        price=100.0,
        size=100,
        notional=1_000_000.0,
        spot=205.0,
        delta=0.5,
        source="convex",
    )
    base.update(kw)
    return TasPrint(**base)


def _seed(session: Session) -> None:
    session.add_all(
        [
            _print(side="buy", notional=1_000_000.0, size=100),
            _print(side="buy", notional=2_000_000.0, size=200),
            _print(
                side="sell", strike=220.0, symbol=".NVDA260717C220", notional=500_000.0, size=50
            ),
            _print(
                root="AMD",
                symbol=".AMD260717C150",
                strike=150.0,
                spot=140.0,
                delta=0.4,
                side="sell",
                notional=1_500_000.0,
                size=150,
            ),
        ]
    )
    session.commit()


def test_rollup_aggregates_name_and_contract(session: Session) -> None:
    _seed(session)
    tas_daily_rollup.run(session, settings=_settings(), backfill=True)

    flows = {f.root: f for f in session.execute(select(TasDailyFlow)).scalars()}
    assert set(flows) == {"NVDA", "AMD"}
    nvda = flows["NVDA"]
    assert nvda.trade_date == _DAY
    assert nvda.prints == 3
    assert nvda.buy_notional == 3_000_000.0
    assert nvda.sell_notional == 500_000.0
    assert nvda.dominant_side == "buy"
    assert nvda.net_dollar_delta > 0  # net call buying = accumulation

    amd = flows["AMD"]
    assert amd.dominant_side == "sell"
    assert amd.net_dollar_delta < 0  # net call selling

    contracts = list(session.execute(select(TasDailyContract)).scalars())
    nvda210 = next(c for c in contracts if c.root == "NVDA" and c.strike == 210.0)
    assert nvda210.n_prints == 2
    assert nvda210.buy_prints == 2
    assert nvda210.dominant_side == "buy"


def test_rollup_is_idempotent(session: Session) -> None:
    _seed(session)
    s = _settings()
    tas_daily_rollup.run(session, settings=s, backfill=True)
    tas_daily_rollup.run(session, settings=s, backfill=True)  # re-run refreshes in place

    flows = list(session.execute(select(TasDailyFlow)).scalars())
    contracts = list(session.execute(select(TasDailyContract)).scalars())
    assert len([f for f in flows if f.root == "NVDA"]) == 1  # no duplicate day rows
    assert len([c for c in contracts if c.root == "NVDA" and c.strike == 210.0]) == 1


def test_rollup_empty_day_noop(session: Session) -> None:
    tas_daily_rollup.run(session, settings=_settings(), target_date=_DAY)
    assert session.execute(select(TasDailyFlow)).first() is None
