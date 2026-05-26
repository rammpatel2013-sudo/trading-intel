"""Tests for the Vol-Richness dashboard data layer (SQLite)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.vol_richness_data import (
    DISPLAY_COLS,
    available_horizons,
    load_latest,
    regime_caption,
    richness_sheet,
    scale_for_display,
)
from trading_intel.memory.models import VolRichness


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    VolRichness.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, **kw) -> None:
    base = dict(
        symbol="X", ts=date(2026, 5, 26), horizon_dte=30, iv_atm=0.2, fcst_rv=0.18,
        vrp_pts=0.02, vrp_pctile=0.5, iv_rank=0.5, term_slope=0.01, skew_25d=0.02,
        regime_zone="low", richness_score=0.5, label="neutral",
    )
    base.update(kw)
    session.add(VolRichness(**base))


def test_load_latest_empty():
    engine = create_engine("sqlite://")
    VolRichness.__table__.create(engine)
    with Session(engine) as s:
        assert load_latest(s).empty


def test_load_latest_only_most_recent_day(session: Session):
    _row(session, symbol="A", ts=date(2026, 5, 25))  # older scan
    _row(session, symbol="B", ts=date(2026, 5, 26))  # latest scan
    session.commit()
    out = load_latest(session)
    assert set(out["symbol"]) == {"B"}  # only the latest ts


def test_richness_sheet_orders_richest_first_cold_last(session: Session):
    _row(session, symbol="RICH", richness_score=0.95, vrp_pts=0.06)
    _row(session, symbol="MID", richness_score=0.50, vrp_pts=0.01)
    _row(session, symbol="COLD", richness_score=None, vrp_pts=0.03, label="cold")
    session.commit()
    sheet = richness_sheet(load_latest(session), horizon=30)
    assert list(sheet.columns) == DISPLAY_COLS
    order = list(sheet["symbol"])
    assert order.index("RICH") < order.index("MID")
    assert order[-1] == "COLD"  # cold (no score) sinks to bottom


def test_richness_sheet_filters_horizon(session: Session):
    _row(session, symbol="A", horizon_dte=30)
    _row(session, symbol="B", horizon_dte=60)
    session.commit()
    frame = load_latest(session)
    assert list(richness_sheet(frame, horizon=60)["symbol"]) == ["B"]
    assert available_horizons(frame) == [30, 60]


def test_richness_sheet_empty_has_columns():
    import pandas as pd
    out = richness_sheet(pd.DataFrame(), horizon=30)
    assert out.empty and list(out.columns) == DISPLAY_COLS


def test_regime_caption_by_zone(session: Session):
    _row(session, regime_zone="high")
    session.commit()
    cap = regime_caption(load_latest(session))
    assert "stress" in cap and "GATED OFF" in cap


def test_regime_caption_empty():
    import pandas as pd
    assert "unavailable" in regime_caption(pd.DataFrame())


def test_scale_for_display_units(session: Session):
    import pandas as pd
    _row(session, symbol="X", iv_atm=0.21, vrp_pts=0.0607, richness_score=0.5, iv_rank=None)
    session.commit()
    scaled = scale_for_display(richness_sheet(load_latest(session), horizon=30))
    r = scaled.iloc[0]
    assert r["iv_atm"] == pytest.approx(21.0)       # decimal -> vol points
    assert r["vrp_pts"] == pytest.approx(6.07)
    assert r["richness_score"] == pytest.approx(50.0)  # 0..1 -> 0..100
    assert pd.isna(r["iv_rank"])                     # None -> NaN, no crash
    assert r["symbol"] == "X"                        # passthrough


def test_scale_for_display_empty():
    import pandas as pd
    out = scale_for_display(pd.DataFrame(columns=DISPLAY_COLS))
    assert out.empty
