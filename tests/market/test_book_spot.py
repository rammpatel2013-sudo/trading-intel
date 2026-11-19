"""book_spot (2026-11-14): the spot that belongs to a 00:00-stamped options book."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.market.book_spot import book_kind, book_spot
from trading_intel.memory.models import GreeksSnapshot, OiChainEod

DAY = datetime(2026, 11, 13)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    for tbl in (GreeksSnapshot, OiChainEod):
        tbl.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(s: Session, source: str) -> None:
    s.add(OiChainEod(symbol="SPX", ts=DAY, expiry=date(2026, 11, 16), strike=7550.0, cp="C", dte=3, oi=1,
                     iv=0.15, source=source))


def _snap(s: Session, when: datetime, spot: float) -> None:
    s.add(GreeksSnapshot(symbol="SPX", ts=when, spot=spot, gex_total=0.0, source="convex"))


def test_eod_book_takes_its_own_days_last_spot(session: Session):
    _row(session, "convex_eod")
    _snap(session, datetime(2026, 11, 12, 15, 59), 7592.06)
    _snap(session, datetime(2026, 11, 13, 15, 59), 7560.97)
    session.commit()
    b = book_spot(session, "SPX", DAY)
    assert b["kind"] == "eod" and b["spot"] == 7560.97 and b["matches_book"]


def test_am_book_takes_the_prior_close_not_a_later_intraday_spot(session: Session):
    _row(session, "convex_am")
    _snap(session, datetime(2026, 11, 12, 15, 59), 7592.06)
    _snap(session, datetime(2026, 11, 13, 10, 5), 7575.0)
    session.commit()
    b = book_spot(session, "SPX", DAY)
    assert b["kind"] == "am" and b["spot"] == 7592.06 and b["matches_book"]


def test_eod_wins_when_both_books_share_the_stamp(session: Session):
    _row(session, "convex_am")
    _row(session, "convex_eod_set")
    session.commit()
    assert book_kind(session, "SPX", DAY) == "eod"


def test_missing_day_spot_is_flagged_not_hidden(session: Session):
    _row(session, "convex_eod")
    _snap(session, datetime(2026, 11, 12, 15, 59), 7592.06)        # collector gap on 11/13
    session.commit()
    b = book_spot(session, "SPX", DAY)
    assert b["spot"] == 7592.06 and not b["matches_book"] and "NO spot" in b["rule"]
