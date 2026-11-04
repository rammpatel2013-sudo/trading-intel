"""Unit tests for the tas_capture_job (Phase 3 NAS tape capture).

SQLite in-memory ``Session`` + a stub ``OptionsDataSource`` returning a fixed
tape frame. Covers: the notional filter, both decode paths (raw symbol vs the
vendor's decoded columns), idempotency, the zeroed-tape guard, and the
market-hours guard.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings
from trading_intel.memory.models import TasPrint
from trading_intel.scheduler.jobs import tas_capture_job


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


class _StubSource:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def time_and_sales(self, symbol: str | None = None, *, limit: int = 500, **kw) -> pd.DataFrame:
        return self._df


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    TasPrint.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _rows(session: Session) -> list[TasPrint]:
    return list(session.execute(select(TasPrint)).scalars())


def _raw_tape() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": [
                "2026-06-03T13:30:01", "2026-06-03T13:30:02", "2026-06-03T13:30:03",
            ],
            "symbol": [".NVDA260619C230", ".AAPL260918P200", ".TSLA260620C400"],
            "price": [2.50, 0.10, 5.0],
            "size": [600, 10, 0],  # NVDA kept; AAPL too small; TSLA zero-size dropped
            "spot": [222.85, 205.0, 355.0],
            "delta": [0.42, -0.30, 0.20],
            "aggressor_side": ["buy", "sell", "buy"],
        }
    )


def test_keeps_big_drops_small_and_zero(session: Session) -> None:
    tas_capture_job.run(session, _StubSource(_raw_tape()), settings=_settings(), force=True)
    rows = _rows(session)
    assert len(rows) == 1
    r = rows[0]
    assert r.root == "NVDA"
    assert r.cp == "C"
    assert r.strike == 230.0
    assert r.expiry == date(2026, 6, 19)
    assert r.side == "buy"
    assert r.notional == pytest.approx(150_000.0)
    assert r.trade_date is not None


def test_decodes_from_vendor_columns(session: Session) -> None:
    df = pd.DataFrame(
        {
            "time": ["2026-06-03T15:55:00"],
            "root": ["NFLX"],
            "opt_kind": ["call"],
            "strike": [595.0],
            "expiration": ["2026-06-05"],
            "price": [0.35],
            "size": [5000],
            "aggressor_side": ["buy"],
        }
    )
    tas_capture_job.run(session, _StubSource(df), settings=_settings(), force=True)
    rows = _rows(session)
    assert len(rows) == 1
    assert rows[0].root == "NFLX"
    assert rows[0].cp == "C"
    assert rows[0].strike == 595.0
    assert rows[0].symbol == ".NFLX260605C595"  # reconstructed from decoded fields


def test_idempotent_on_rerun(session: Session) -> None:
    src = _StubSource(_raw_tape())
    tas_capture_job.run(session, src, settings=_settings(), force=True)
    tas_capture_job.run(session, src, settings=_settings(), force=True)
    assert len(_rows(session)) == 1  # same print not double-counted


def test_zeroed_tape_is_skipped(session: Session) -> None:
    df = pd.DataFrame(
        {
            "time": ["2026-06-03T17:00:00"],
            "symbol": [".NVDA260619C230"],
            "price": [0.0],
            "size": [0],
            "aggressor_side": ["undefined"],
        }
    )
    tas_capture_job.run(session, _StubSource(df), settings=_settings(), force=True)
    assert _rows(session) == []


def test_excludes_index_roots(session: Session) -> None:
    # SPX is in the default TAS_EXCLUDE_ROOTS; under the $5k SPX tape floor it is dropped,
    # the single name is kept.
    df = pd.DataFrame(
        {
            "time": ["2026-06-03T13:30:01", "2026-06-03T13:30:02"],
            "symbol": [".SPX260620C6000", ".NVDA260619C230"],
            "price": [0.40, 2.50],
            "size": [100, 600],
            "aggressor_side": ["buy", "buy"],
        }
    )
    tas_capture_job.run(session, _StubSource(df), settings=_settings(), force=True)
    rows = _rows(session)
    assert len(rows) == 1
    assert rows[0].root == "NVDA"


def test_off_hours_guard_blocks_write(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tas_capture_job, "is_market_hours", lambda now: False)
    tas_capture_job.run(session, _StubSource(_raw_tape()), settings=_settings(), force=False)
    assert _rows(session) == []


def test_spx_tape_floor_quote_loc_and_vendor_legs(session: Session) -> None:
    """2026-11-04: SPX prints from $5k are kept (put buy-ratio), each print records where it
    traded vs its own quote, and vendor-flagged spread legs at the same instant share a group."""
    df = pd.DataFrame(
        {
            "time": ["2026-06-03T13:30:01.100", "2026-06-03T13:30:01.100", "2026-06-03T13:30:05", "2026-06-03T13:30:06"],
            "symbol": [".SPXW260603P5900", ".SPXW260603P5880", ".SPXW260603P5850", ".QQQ260603P500"],
            "price": [4.0, 2.05, 1.10, 0.50],
            "size": [20, 30, 50, 100],
            "aggressor_side": ["buy", "sell", "sell", "buy"],
            "bid_price": [3.9, 2.0, 1.10, 0.45],
            "ask_price": [4.0, 2.1, 1.20, 0.55],
            "spread_leg": [True, True, False, False],
        }
    )
    tas_capture_job.run(session, _StubSource(df), settings=_settings(), force=True)
    rows = sorted(_rows(session), key=lambda r: r.strike)
    assert [r.root for r in rows] == ["SPXW", "SPXW", "SPXW"]          # QQQ $5k print stays under its floor
    by_k = {r.strike: r for r in rows}
    assert by_k[5900].quote_loc == "at_ask" and by_k[5850].quote_loc == "at_bid"
    assert by_k[5880].quote_loc == "mid"
    assert by_k[5900].leg_group is not None and by_k[5900].leg_group == by_k[5880].leg_group
    assert by_k[5850].leg_group is None and by_k[5850].spread_leg is False


def test_quote_loc_rules() -> None:
    q = tas_capture_job.quote_loc
    assert q(1.0, None, 1.1) is None
    assert q(1.0, 1.0, 1.2) == "at_bid" and q(1.2, 1.0, 1.2) == "at_ask"
    assert q(1.1, 1.0, 1.2) == "mid" and q(1.05, 1.0, 1.2) == "inside"
    assert q(1.3, 1.0, 1.2) == "outside" and q(1.0, 1.0, 0) == "no_quote"
