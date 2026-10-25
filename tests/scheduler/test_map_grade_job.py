"""The post-close grading job, on a tiny sqlite map_daily + quotes_daily."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import MapDaily, QuoteDaily
from trading_intel.scheduler.jobs import map_grade as job

TODAY = date(2026, 10, 26)


def _session() -> Session:
    engine = create_engine("sqlite://")
    MapDaily.__table__.create(engine)
    QuoteDaily.__table__.create(engine)
    return Session(engine)


def _card(s: Session, d: date, **over) -> MapDaily:
    row = MapDaily(
        symbol="SPX", date=d, spot=6000.0, floor=5900.0, cap=6100.0,
        range70_lo=5930.0, range70_hi=6095.0, levels_firm=True, **over
    )
    s.add(row)
    s.commit()
    return row


def _quote(s: Session, d: date, high, low, close) -> None:
    s.add(QuoteDaily(symbol="SPX", date=d, open=close, high=high, low=low,
                     close=close, volume=0))
    s.commit()


def test_grades_a_session_from_the_daily_bar():
    s = _session()
    d = date(2026, 10, 23)
    _card(s, d)
    _quote(s, d, high=6050, low=5880, close=5960)

    assert job.run(s, today=TODAY) == 1
    row = s.query(MapDaily).one()
    assert row.bucket == "between"
    assert row.floor_touched is True and row.floor_held is True
    assert row.wall_broke is False
    assert row.inside_range70 is True
    assert (row.high, row.low, row.close) == (6050, 5880, 5960)
    assert row.graded_at is not None


def test_waits_when_the_close_is_not_banked_yet():
    s = _session()
    _card(s, date(2026, 10, 23))
    assert job.run(s, today=TODAY) == 0
    assert s.query(MapDaily).one().bucket is None  # still pending, not failed


def test_is_idempotent_and_never_regrades():
    s = _session()
    d = date(2026, 10, 23)
    _card(s, d)
    _quote(s, d, high=6050, low=5950, close=6000)
    assert job.run(s, today=TODAY) == 1
    first = s.query(MapDaily).one().graded_at
    assert job.run(s, today=TODAY) == 0  # nothing left pending
    assert s.query(MapDaily).one().graded_at == first


def test_lookback_window_excludes_stale_rows():
    s = _session()
    old = date(2026, 9, 1)
    _card(s, old)
    _quote(s, old, high=6050, low=5950, close=6000)
    assert job.run(s, today=TODAY, lookback_days=14) == 0
    assert job.run(s, today=TODAY, lookback_days=120) == 1


def test_row_without_levels_is_not_graded():
    s = _session()
    d = date(2026, 10, 23)
    s.add(MapDaily(symbol="SPX", date=d, spot=6000.0))  # no floor/cap banked
    s.commit()
    _quote(s, d, high=6050, low=5950, close=6000)
    assert job.run(s, today=TODAY) == 0
    assert s.query(MapDaily).one().bucket is None


def test_grades_several_sessions_in_one_pass():
    s = _session()
    for i, (d, close) in enumerate(
        [(date(2026, 10, 21), 5870), (date(2026, 10, 22), 6000), (date(2026, 10, 23), 6150)]
    ):
        _card(s, d)
        _quote(s, d, high=close + 20, low=close - 20, close=close)
    assert job.run(s, today=TODAY) == 3
    buckets = [r.bucket for r in s.query(MapDaily).order_by(MapDaily.date).all()]
    assert buckets == ["below", "between", "above"]


def test_other_symbols_are_left_alone():
    s = _session()
    d = date(2026, 10, 23)
    _card(s, d)
    s.add(MapDaily(symbol="NDX", date=d, floor=1.0, cap=2.0))
    s.commit()
    _quote(s, d, high=6050, low=5950, close=6000)
    job.run(s, today=TODAY, symbol="SPX")
    assert s.query(MapDaily).filter_by(symbol="NDX").one().bucket is None
