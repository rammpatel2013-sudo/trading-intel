"""Tests for the day-over-day OI/flow change analytics — SQLite, no Postgres."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.oi_changes import (
    _rows_to_frame as rows_to_frame,
)
from trading_intel.dashboard.oi_changes import (
    build_oi_change_frame,
    classify_positioning,
    load_oi_change_frame,
    summarize_oi_change,
    top_oi_changes,
)
from trading_intel.memory.models import OiChainEod

_EXP = date(2026, 5, 26)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    OiChainEod.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _add(session: Session, ts: datetime, *, strike: float, cp: str, oi: int,
         oi_change: int | None, volume: int, gxoi: float, iv: float | None = None) -> None:
    session.add(
        OiChainEod(symbol="SPX", ts=ts, expiry=_EXP, strike=strike, cp=cp, dte=4,
                   oi=oi, oi_change=oi_change, volume=volume, gxoi=gxoi, iv=iv,
                   source="convex_eod")
    )


def test_diff_oi_volume_conversion_and_signed_gex(session: Session):
    prev = datetime(2026, 5, 21, 0, 0, tzinfo=UTC)
    curr = datetime(2026, 5, 22, 0, 0, tzinfo=UTC)
    # call 7500: OI 1000 -> 1200 (ΔOI +200), volume 400 -> conversion 0.5; gxoi +
    _add(session, prev, strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6)
    _add(session, curr, strike=7500, cp="C", oi=1200, oi_change=150, volume=400, gxoi=1.2e6)
    # put 7400: OI 900 -> 800 (ΔOI -100); gxoi sign flips negative
    _add(session, prev, strike=7400, cp="P", oi=900, oi_change=0, volume=200, gxoi=4.0e5)
    _add(session, curr, strike=7400, cp="P", oi=800, oi_change=-50, volume=250, gxoi=3.0e5)
    session.commit()

    frame = load_oi_change_frame(session, "SPX")
    assert frame is not None and len(frame) == 2

    call = frame[(frame["strike"] == 7500) & (frame["cp"] == "C")].iloc[0]
    assert call["d_oi"] == pytest.approx(200.0)
    assert call["conversion"] == pytest.approx(200.0 / 400.0)
    assert call["d_gex_contrib"] == pytest.approx(1.2e6 - 1.0e6)  # call: +gxoi
    assert call["oi_change_vendor"] == pytest.approx(150.0)

    put = frame[(frame["strike"] == 7400) & (frame["cp"] == "P")].iloc[0]
    assert put["d_oi"] == pytest.approx(-100.0)
    # put gex_contrib is -gxoi, so Δ = -(3.0e5) - (-(4.0e5)) = +1.0e5
    assert put["d_gex_contrib"] == pytest.approx(-3.0e5 - (-4.0e5))


def test_summary_rollup_is_descriptive(session: Session):
    prev = datetime(2026, 5, 21, 0, 0, tzinfo=UTC)
    curr = datetime(2026, 5, 22, 0, 0, tzinfo=UTC)
    _add(session, prev, strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6)
    _add(session, curr, strike=7500, cp="C", oi=1300, oi_change=300, volume=400, gxoi=1.5e6)
    _add(session, prev, strike=7400, cp="P", oi=900, oi_change=0, volume=200, gxoi=4.0e5)
    _add(session, curr, strike=7400, cp="P", oi=850, oi_change=-50, volume=250, gxoi=3.5e5)
    session.commit()

    summary = summarize_oi_change(load_oi_change_frame(session, "SPX"))
    assert summary.call_d_oi == pytest.approx(300.0)
    assert summary.put_d_oi == pytest.approx(-50.0)
    assert summary.n_strikes == 2
    assert "not a trade signal" in summary.note.lower()


def test_new_strike_treated_as_full_delta(session: Session):
    prev = datetime(2026, 5, 21, 0, 0, tzinfo=UTC)
    curr = datetime(2026, 5, 22, 0, 0, tzinfo=UTC)
    # only present today -> ΔOI == today's OI (prev filled 0)
    _add(session, prev, strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6)
    _add(session, curr, strike=7500, cp="C", oi=1000, oi_change=0, volume=10, gxoi=1.0e6)
    _add(session, curr, strike=7600, cp="C", oi=500, oi_change=500, volume=500, gxoi=6.0e5)
    session.commit()

    frame = load_oi_change_frame(session, "SPX")
    new = frame[frame["strike"] == 7600].iloc[0]
    assert new["oi_prev"] == pytest.approx(0.0)
    assert new["d_oi"] == pytest.approx(500.0)


def test_needs_two_snapshots(session: Session):
    _add(session, datetime(2026, 5, 22, 0, 0, tzinfo=UTC),
         strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6)
    session.commit()
    assert load_oi_change_frame(session, "SPX") is None


def _frame(*specs: tuple[float, str, int]):
    """Build a snapshot frame from (strike, cp, oi) specs via the ORM mapper."""
    objs = [
        OiChainEod(symbol="SPX", ts=datetime(2026, 5, 22), expiry=_EXP, strike=k,
                   cp=cp, oi=oi, oi_change=0, volume=100, gxoi=1.0e6)
        for k, cp, oi in specs
    ]
    return rows_to_frame(objs)


def test_top_changes_ranks_by_abs():
    prev = _frame((7500.0, "C", 900), (7400.0, "P", 790))
    curr = _frame((7500.0, "C", 1200), (7400.0, "P", 800))  # ΔOI +300 vs +10
    frame = build_oi_change_frame(prev, curr)
    top = top_oi_changes(frame, by="d_oi", n=1)
    assert len(top) == 1 and top.iloc[0]["cp"] == "C"


def test_classify_positioning_branches():
    assert classify_positioning(200, 0.01) == "opening, demand-led (IV up)"
    assert classify_positioning(200, -0.01) == "opening, supply-led (IV down)"
    assert classify_positioning(-100, -0.01) == "closing/unwind (IV down)"
    assert classify_positioning(-100, 0.01) == "closing into firmer IV"
    # ΔIV unknown -> fall back to OI-only labels
    assert classify_positioning(200, None) == "opening interest"
    assert classify_positioning(-100, float("nan")) == "closing/unwind"
    assert classify_positioning(0, 0.0) == "little change"


def test_d_iv_and_positioning_in_frame(session: Session):
    prev = datetime(2026, 5, 21, 0, 0, tzinfo=UTC)
    curr = datetime(2026, 5, 22, 0, 0, tzinfo=UTC)
    # call 7500: OI 1000 -> 1200 (opening), IV 0.18 -> 0.20 (firming) -> demand-led
    _add(session, prev, strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6, iv=0.18)
    _add(session, curr, strike=7500, cp="C", oi=1200, oi_change=150, volume=400, gxoi=1.2e6, iv=0.20)
    # put 7400: OI 900 -> 800 (closing), IV 0.22 -> 0.20 (easing) -> closing/unwind
    _add(session, prev, strike=7400, cp="P", oi=900, oi_change=0, volume=200, gxoi=4.0e5, iv=0.22)
    _add(session, curr, strike=7400, cp="P", oi=800, oi_change=-50, volume=250, gxoi=3.0e5, iv=0.20)
    session.commit()

    frame = load_oi_change_frame(session, "SPX")
    call = frame[(frame["strike"] == 7500) & (frame["cp"] == "C")].iloc[0]
    assert call["d_iv"] == pytest.approx(0.02)
    assert call["positioning"] == "opening, demand-led (IV up)"
    put = frame[(frame["strike"] == 7400) & (frame["cp"] == "P")].iloc[0]
    assert put["d_iv"] == pytest.approx(-0.02)
    assert put["positioning"] == "closing/unwind (IV down)"

    summary = summarize_oi_change(frame)
    assert summary.mean_d_iv == pytest.approx(0.0)  # +0.02 and -0.02 average to 0
    assert "iv" in summary.note.lower()


def test_new_strike_iv_unknown_uses_oi_only_label(session: Session):
    prev = datetime(2026, 5, 21, 0, 0, tzinfo=UTC)
    curr = datetime(2026, 5, 22, 0, 0, tzinfo=UTC)
    _add(session, prev, strike=7500, cp="C", oi=1000, oi_change=0, volume=300, gxoi=1.0e6, iv=0.18)
    _add(session, curr, strike=7500, cp="C", oi=1000, oi_change=0, volume=10, gxoi=1.0e6, iv=0.18)
    _add(session, curr, strike=7600, cp="C", oi=500, oi_change=500, volume=500, gxoi=6.0e5, iv=0.21)
    session.commit()

    frame = load_oi_change_frame(session, "SPX")
    new = frame[frame["strike"] == 7600].iloc[0]
    assert pd.isna(new["d_iv"])  # no prior IV for a brand-new strike
    assert new["positioning"] == "opening interest"
