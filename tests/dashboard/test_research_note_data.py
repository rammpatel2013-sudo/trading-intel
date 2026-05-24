"""Tests for the research-note reader (SQLite, no network)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.research_note_data import latest_research_note
from trading_intel.memory.models import ResearchNote


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    ResearchNote.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_latest_returns_newest(session: Session):
    session.add(ResearchNote(symbol="AAPL", as_of=date(2026, 5, 20), note_md="old", sources="pdf"))
    session.add(ResearchNote(symbol="AAPL", as_of=date(2026, 5, 23), note_md="new", sources="pdf,10-K"))
    session.commit()
    n = latest_research_note(session, "AAPL")
    assert n is not None
    assert n.note_md == "new" and n.as_of == date(2026, 5, 23)


def test_none_for_missing(session: Session):
    assert latest_research_note(session, "ZZZ") is None
