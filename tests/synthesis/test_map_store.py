"""Projecting a MAP block into a map_daily row, and reading history back."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import MapDaily
from trading_intel.synthesis import map_store as st


def _block() -> dict:
    return {
        "symbol": "SPX",
        "spot": 6012.4,
        "regime": {"long_gamma": False, "net_gamma_dollars": -1.4e9},
        "switches": [
            {"name": "whole-book flip", "value": 6050.0},
            {"name": "same-day flip", "value": 5990.0},
        ],
        "wall_views": [
            {"label": "This week", "put_wall": 5900.0, "call_wall": 6100.0},
            {"label": "Today only", "put_wall": 5950.0, "call_wall": 6050.0},
        ],
        "atm_iv": 0.139,
        "cones": [
            {"delta": 0.25, "band_pct": 50, "lower": 5960.0, "upper": 6065.0},
            {"delta": 0.15, "band_pct": 70, "lower": 5930.0, "upper": 6095.0},
            {"delta": 0.10, "band_pct": 80, "lower": 5905.0, "upper": 6120.0},
        ],
        "fragility": {
            "firm": False, "tol": 13.0,
            "legs": [{"name": "walls", "firm": True, "move": 4.0},
                     {"name": "flip", "firm": False, "move": 22.0}],
        },
    }


def _session() -> Session:
    engine = create_engine("sqlite://")
    MapDaily.__table__.create(engine)
    return Session(engine)


def test_morning_values_picks_the_right_fields():
    v = st.morning_values(_block())
    assert v["book_flip"] == 6050.0 and v["day_flip"] == 5990.0
    assert v["floor"] == 5900.0 and v["cap"] == 6100.0
    assert v["day_floor"] == 5950.0 and v["day_cap"] == 6050.0
    assert v["range70_lo"] == 5930.0 and v["range70_hi"] == 6095.0  # the 15Δ cone
    assert v["walls_firm"] is True and v["flip_firm"] is False
    assert v["levels_firm"] is False
    assert v["atm_iv"] == pytest.approx(0.139)


def test_morning_values_survives_a_hollow_block():
    v = st.morning_values({"symbol": "SPX"})
    assert set(v) == set(st._MORNING_COLS)
    assert all(x is None for x in v.values())


def test_range70_falls_back_to_none_without_a_70_cone():
    b = _block()
    b["cones"] = [{"delta": 0.25, "band_pct": 50, "lower": 1.0, "upper": 2.0}]
    v = st.morning_values(b)
    assert v["range70_lo"] is None and v["range70_hi"] is None


def test_record_morning_declines_an_empty_block():
    assert st.record_morning(_session(), {}, today=date(2026, 10, 26)) is False


def test_graded_count_and_recent_rows():
    s = _session()
    for i, d in enumerate(
        [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    ):
        s.add(MapDaily(symbol="SPX", date=d, bucket="between", inside_range70=True))
    s.add(MapDaily(symbol="SPX", date=date(2026, 10, 23)))  # ungraded
    s.add(MapDaily(symbol="NDX", date=date(2026, 10, 22), bucket="below"))
    s.commit()

    assert st.graded_count(s, "SPX") == 3
    rows = st.recent_rows(s, "SPX", days=2)
    assert [r["date"] for r in rows] == [date(2026, 10, 22), date(2026, 10, 21)]
    assert st.recent_rows(s, "SPX", days=10, before=date(2026, 10, 21))[0]["date"] == date(
        2026, 10, 21
    )


def test_ungraded_rows_is_the_jobs_work_list():
    s = _session()
    s.add(MapDaily(symbol="SPX", date=date(2026, 10, 22), bucket="between"))
    s.add(MapDaily(symbol="SPX", date=date(2026, 10, 23)))
    s.commit()
    pending = st.ungraded_rows(s, "SPX")
    assert [r.date for r in pending] == [date(2026, 10, 23)]
    assert st.ungraded_rows(s, "SPX", since=date(2026, 10, 24)) == []


def test_readers_degrade_to_empty_on_a_broken_session():
    class _Bad:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert st.graded_count(_Bad(), "SPX") == 0
    assert st.recent_rows(_Bad(), "SPX") == []
