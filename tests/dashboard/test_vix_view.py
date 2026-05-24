"""Tests for the VIX view helpers — SQLite, no network."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.vix_view import (
    classify_term_structure,
    classify_zone,
    load_vix_history,
    near_term_stress,
    term_structure_frame,
    term_structure_from_row,
    vvix_vix_ratio,
    zone_caption,
)
from trading_intel.memory.models import VixData


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    VixData.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_classify_zone():
    assert classify_zone(10.0) == "low"
    assert classify_zone(22.0) == "mid"  # boundary: not < 22 -> mid
    assert classify_zone(32.0) == "mid"
    assert classify_zone(40.0) == "high"
    assert classify_zone(None) is None


def test_zone_caption_mentions_regime():
    assert "carry" in zone_caption(15.0)
    assert "fragility" in zone_caption(27.0)
    assert "crisis" in zone_caption(40.0)
    assert "unavailable" in zone_caption(None)


def test_load_vix_history_sorted_oldest_first(session: Session):
    session.add(VixData(date=date(2026, 5, 22), vix=20.0, vega_zone="low"))
    session.add(VixData(date=date(2026, 5, 21), vix=18.0, vega_zone="low"))
    session.commit()
    hist = load_vix_history(session, days=30)
    assert list(hist["date"]) == [date(2026, 5, 21), date(2026, 5, 22)]
    assert hist.iloc[-1]["vix"] == 20.0


def test_load_vix_history_empty(session: Session):
    assert load_vix_history(session).empty


def test_term_structure_frame_orders_and_drops_none():
    frame = term_structure_frame({"VIX6M": 23.0, "VIX9D": 18.0, "VIX": 20.0, "VIX3M": None})
    # ordered by DTE, the None tenor dropped
    assert list(frame["tenor"]) == ["VIX9D", "VIX", "VIX6M"]
    assert list(frame["dte"]) == [9, 30, 182]


def test_term_structure_frame_empty():
    assert term_structure_frame(None).empty
    assert term_structure_frame({}).empty


def test_term_structure_from_row_builds_curve():
    row = {"vix9d": 14.0, "vix": 16.0, "vix3m": 20.0, "vix6m": 22.0}
    frame = term_structure_from_row(row)
    assert list(frame["tenor"]) == ["VIX9D", "VIX", "VIX3M", "VIX6M"]
    assert list(frame["level"]) == [14.0, 16.0, 20.0, 22.0]


def test_term_structure_from_row_handles_missing():
    assert term_structure_from_row(None).empty
    # A row with no tenor values yields an empty curve.
    assert term_structure_from_row({"vix9d": None, "vix": None}).empty


def test_classify_term_structure():
    contango = term_structure_from_row({"vix9d": 14.0, "vix": 16.0, "vix3m": 20.0, "vix6m": 22.0})
    backward = term_structure_from_row({"vix9d": 30.0, "vix": 26.0, "vix3m": 22.0, "vix6m": 20.0})
    flat = term_structure_from_row({"vix9d": 18.0, "vix": 18.1, "vix3m": 18.2, "vix6m": 18.3})
    assert classify_term_structure(contango) == "contango"
    assert classify_term_structure(backward) == "backwardation"
    assert classify_term_structure(flat) == "flat"
    assert classify_term_structure(term_structure_from_row(None)) is None


def test_vvix_vix_ratio():
    assert vvix_vix_ratio(90.0, 18.0) == pytest.approx(5.0)
    assert vvix_vix_ratio(None, 18.0) is None
    assert vvix_vix_ratio(90.0, 0) is None


def test_near_term_stress():
    assert near_term_stress(20.0, 16.0) == pytest.approx(1.25)  # backwardation warning
    assert near_term_stress(14.0, 16.0) == pytest.approx(0.875)  # calm
    assert near_term_stress(None, 16.0) is None
