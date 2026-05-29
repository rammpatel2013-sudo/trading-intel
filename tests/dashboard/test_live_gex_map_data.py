"""Tests for the live gamma/charm/vanna map data layer (SQLite)."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.dashboard.live_gex_map_data import (
    available_expiries,
    composite_matrix,
    composite_profile,
    exposure_matrix,
    filter_expiry_scope,
    filter_to_expiries,
    latest_profile,
    load_live_gex_day,
    session_fraction_remaining,
    spot_path,
)
from trading_intel.memory.models import LiveGex


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    LiveGex.__table__.create(engine)
    with Session(engine) as s:
        yield s


def _row(session: Session, **kw) -> None:
    base = dict(
        symbol="SPX", ts=datetime(2026, 5, 26, 15, 0), strike=7500.0, cp="C",
        source="convex", spot=7506.0, gxoi=1e6, dxoi=2e6, oi=5000.0, vanna=0.03, charm=-0.01,
    )
    base.update(kw)
    session.add(LiveGex(**base))


def test_load_only_latest_day(session: Session):
    _row(session, ts=datetime(2026, 5, 25, 15, 0))  # prior session
    _row(session, ts=datetime(2026, 5, 26, 15, 0))
    session.commit()
    out = load_live_gex_day(session, "SPX")
    assert len(out) == 1 and out.iloc[0]["ts"] == datetime(2026, 5, 26, 15, 0)


def test_exposure_matrix_signs_and_shape(session: Session):
    ts1, ts2 = datetime(2026, 5, 26, 15, 0), datetime(2026, 5, 26, 15, 10)
    # one call + one put at the same strike, two snapshots
    _row(session, ts=ts1, strike=7500.0, cp="C", gxoi=1e6)
    _row(session, ts=ts1, strike=7500.0, cp="P", gxoi=4e5)
    _row(session, ts=ts2, strike=7500.0, cp="C", gxoi=1.2e6)
    session.commit()
    frame = load_live_gex_day(session, "SPX")
    mat = exposure_matrix(frame, "gamma")
    assert list(mat.index) == [7500.0]
    assert list(mat.columns) == [ts1, ts2]
    # net gamma at ts1 = call(+1e6) - put(4e5) = 6e5
    assert mat.loc[7500.0, ts1] == pytest.approx(6e5)
    assert mat.loc[7500.0, ts2] == pytest.approx(1.2e6)


def test_charm_vanna_use_greek_times_oi(session: Session):
    _row(session, cp="C", charm=-0.01, vanna=0.03, oi=5000.0)
    session.commit()
    frame = load_live_gex_day(session, "SPX")
    # call sign +1 -> charm*oi = -0.01*5000 = -50 ; vanna*oi = 0.03*5000 = 150
    assert latest_profile(frame, "charm").iloc[0]["exposure"] == pytest.approx(-50.0)
    assert latest_profile(frame, "vanna").iloc[0]["exposure"] == pytest.approx(150.0)


def test_spot_path_and_empty(session: Session):
    _row(session, ts=datetime(2026, 5, 26, 15, 0), spot=7506.0)
    session.commit()
    sp = spot_path(load_live_gex_day(session, "SPX"))
    assert list(sp["spot"]) == [7506.0]
    assert exposure_matrix(load_live_gex_day(session, "NOPE"), "gamma").empty


def test_session_fraction_remaining_boundaries():
    assert session_fraction_remaining(datetime(2026, 5, 26, 9, 30)) == 1.0
    assert session_fraction_remaining(datetime(2026, 5, 26, 16, 0)) == 0.0
    assert session_fraction_remaining(datetime(2026, 5, 26, 17, 30)) == 0.0  # after close clamps
    assert session_fraction_remaining(datetime(2026, 5, 26, 12, 45)) == pytest.approx(0.5)


def test_composite_charm_decays_to_zero_at_close(session: Session):
    # same strike at the open (charm live) and at the 16:00 close (charm -> 0)
    open_ts, close_ts = datetime(2026, 5, 26, 9, 30), datetime(2026, 5, 26, 16, 0)
    for ts in (open_ts, close_ts):
        _row(session, ts=ts, strike=7500.0, cp="C", gxoi=1e6, oi=5000.0, vanna=0.03, charm=-0.02)
        _row(session, ts=ts, strike=7500.0, cp="P", gxoi=8e5, oi=4000.0, vanna=-0.02, charm=0.015)
    session.commit()
    frame = load_live_gex_day(session, "SPX")

    # charm exposure must be present at the open but exactly zero at the close
    charm_mat = exposure_matrix(frame, "charm").mul(
        {ts: session_fraction_remaining(ts) for ts in [open_ts, close_ts]}, axis=1
    )
    assert (charm_mat[close_ts] == 0.0).all()
    assert (charm_mat[open_ts] != 0.0).any()

    comp = composite_matrix(frame)
    assert list(comp.columns) == [open_ts, close_ts]
    assert comp.to_numpy().shape == (1, 2)
    # composite is finite and the latest profile aligns to the last snapshot
    prof = composite_profile(frame)
    assert list(prof.columns) == ["strike", "exposure"]
    assert prof.iloc[0]["strike"] == 7500.0


def test_composite_empty():
    assert composite_matrix(None).empty
    assert composite_profile(None).empty


def test_filter_expiry_scope_0dte_vs_all(session: Session):
    sess = datetime(2026, 5, 26, 15, 0)
    _row(session, ts=sess, strike=7500.0, cp="C", expiry=date(2026, 5, 26))  # 0DTE
    _row(session, ts=sess, strike=7500.0, cp="C", expiry=date(2026, 7, 17))  # far
    session.commit()
    frame = load_live_gex_day(session, "SPX")
    assert len(filter_expiry_scope(frame, "All")) == 2  # no-op
    zdte = filter_expiry_scope(frame, "0DTE")
    assert len(zdte) == 1
    assert pd_date(zdte) == date(2026, 5, 26)


def pd_date(frame):  # noqa: ANN001
    import pandas as pd

    return pd.to_datetime(frame.iloc[0]["expiry"]).date()


def test_flow_adjusted_exposure(session: Session):
    # net_flow = 1200 - 200 = 1000 ; oi_eff = 4000 + 1000 = 5000
    _row(session, cp="C", gxoi=1e6, gamma=0.01, oi=4000.0, charm=-0.01, vanna=0.03,
         volm_buy=1200.0, volm_sell=200.0)
    session.commit()
    frame = load_live_gex_day(session, "SPX")
    g = latest_profile(frame, "gamma").iloc[0]["exposure"]
    c = latest_profile(frame, "charm").iloc[0]["exposure"]
    v = latest_profile(frame, "vanna").iloc[0]["exposure"]
    assert g == pytest.approx(1e6 + 0.01 * 1000)   # gxoi + gamma*net_flow
    assert c == pytest.approx(-0.01 * 5000)         # charm * oi_eff
    assert v == pytest.approx(0.03 * 5000)          # vanna * oi_eff


def test_available_and_filter_to_expiries(session: Session):
    sess = datetime(2026, 5, 26, 15, 0)
    _row(session, ts=sess, strike=7500.0, cp="C", expiry=date(2026, 5, 26))
    _row(session, ts=sess, strike=7600.0, cp="C", expiry=date(2026, 5, 29))
    _row(session, ts=sess, strike=7700.0, cp="C", expiry=date(2026, 7, 17))
    session.commit()
    frame = load_live_gex_day(session, "SPX")
    assert available_expiries(frame) == [date(2026, 5, 26), date(2026, 5, 29), date(2026, 7, 17)]
    kept = filter_to_expiries(frame, [date(2026, 5, 26), date(2026, 5, 29)])
    assert set(kept["strike"]) == {7500.0, 7600.0}
    # empty selection -> unchanged
    assert len(filter_to_expiries(frame, [])) == 3
