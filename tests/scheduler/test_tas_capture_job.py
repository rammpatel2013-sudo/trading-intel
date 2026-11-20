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
    monkeypatch.setattr(tas_capture_job, "tape_session", lambda now: None)
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
    assert q(1.1, 1.0, 1.2) == "mid" and q(1.05, 1.0, 1.2) == "in_lo" and q(1.16, 1.0, 1.2) == "in_hi"
    assert q(1.3, 1.0, 1.2) == "outside" and q(1.0, 1.0, 0) == "no_quote"


# --- 2026-11-19 (D13): the 2-s guess keeps only clusters that look like one structure ----------------
def _leg(root: str, sec: float, size: float, vendor: bool = False) -> dict:
    from datetime import datetime, timedelta

    return {"root": root, "ts": datetime(2026, 11, 18, 10, 0) + timedelta(seconds=sec), "size": size,
            "spread_leg": vendor, "leg_group": None}


def test_guess_groups_matched_sizes() -> None:
    from trading_intel.scheduler.jobs.tas_capture_job import _assign_leg_groups

    recs = [_leg("SPY", 0, 100), _leg("SPY", 1, 200)]
    _assign_leg_groups(recs)
    assert recs[0]["leg_group"] and recs[0]["leg_group"] == recs[1]["leg_group"]


def test_guess_skips_mixed_sizes_and_big_clusters() -> None:
    from trading_intel.scheduler.jobs.tas_capture_job import _assign_leg_groups

    mixed = [_leg("NVDA", 0, 100), _leg("NVDA", 1, 37)]
    big = [_leg("QQQ", i * 0.3, 50) for i in range(5)]
    _assign_leg_groups(mixed + big)
    assert all(r["leg_group"] is None for r in mixed + big)


def test_vendor_groups_ignore_the_size_rule() -> None:
    from trading_intel.scheduler.jobs.tas_capture_job import _assign_leg_groups

    recs = [_leg("SPXW", 0, 10, True), _leg("SPXW", 0, 7, True), _leg("SPXW", 0, 3, True)]
    _assign_leg_groups(recs)
    assert recs[0]["leg_group"] and len({r["leg_group"] for r in recs}) == 1


def test_roll_across_spx_and_spxw_roots_is_one_group() -> None:
    """2026-11-20: 11/19 roll printed Nov SPXW legs + Jan SPX legs ~100 ms apart, vendor-flagged."""
    from trading_intel.scheduler.jobs.tas_capture_job import _assign_leg_groups

    recs = [_leg("SPX", 0.0, 1347, True), _leg("SPXW", 0.0, 1347, True),
            _leg("SPX", 0.1, 1347, True), _leg("SPXW", 0.1, 1347, True)]
    _assign_leg_groups(recs)
    assert recs[0]["leg_group"] and len({r["leg_group"] for r in recs}) == 1


def test_unflagged_spx_spxw_legs_group_by_family() -> None:
    from trading_intel.scheduler.jobs.tas_capture_job import _assign_leg_groups

    recs = [_leg("SPX", 0.0, 500), _leg("SPXW", 0.5, 500)]
    _assign_leg_groups(recs)
    assert recs[0]["leg_group"] and recs[0]["leg_group"] == recs[1]["leg_group"]


# --- 2026-11-19 (A1b): every tape session is captured and tagged ------------------------------------
def test_prints_tagged_with_session_and_gth_goes_to_next_day(session: Session) -> None:
    df = pd.DataFrame(
        {
            "time": ["2026-11-19T20:30:00", "2026-11-20T08:10:00", "2026-11-20T10:00:00", "2026-11-20T16:20:00"],
            "symbol": [".NVDA261218C230", ".NVDA261218C235", ".NVDA261218C240", ".NVDA261218C245"],
            "price": [2.5, 2.5, 2.5, 2.5],
            "size": [600, 600, 600, 600],
            "spot": [222.85] * 4,
            "delta": [0.42] * 4,
            "aggressor_side": ["buy"] * 4,
        }
    )
    tas_capture_job.run(session, _StubSource(df), settings=_settings(), force=True)
    got = sorted((r.strike, r.session, r.trade_date) for r in _rows(session))
    assert got == [
        (230.0, "gth", date(2026, 11, 20)),
        (235.0, "pre", date(2026, 11, 20)),
        (240.0, "rth", date(2026, 11, 20)),
        (245.0, "curb", date(2026, 11, 20)),
    ]
