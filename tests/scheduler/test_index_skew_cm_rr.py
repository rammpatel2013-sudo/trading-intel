"""2026-11-22: SPX 25d RR falls back to vol_surface_cm (30d rung) + one-shot history backfill."""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trading_intel.memory.models import IndexSkewDaily, VolSurfaceCM
from trading_intel.scheduler.jobs import index_skew


def _session() -> Session:
    eng = create_engine("sqlite://")
    IndexSkewDaily.__table__.create(eng)
    VolSurfaceCM.__table__.create(eng)
    return Session(eng)


def _cm(s: Session, day: date, put: float, call: float) -> None:
    s.add(VolSurfaceCM(symbol="SPX", ts=day, dte=30, delta=25.0, side="put", iv=put))
    s.add(VolSurfaceCM(symbol="SPX", ts=day, dte=30, delta=25.0, side="call", iv=call))
    s.add(VolSurfaceCM(symbol="SPX", ts=day, dte=7, delta=25.0, side="put", iv=0.9))  # other rung ignored


def test_cm_rr_reads_put_minus_call_at_30d() -> None:
    s = _session()
    _cm(s, date(2026, 11, 20), 0.398, 0.266)
    s.flush()
    rr = index_skew._cm_rr_for(s, date(2026, 11, 20))
    assert abs(rr - 0.132) < 1e-9
    # Monday read still finds Friday (within 4 days); exact mode does not
    assert index_skew._cm_rr_for(s, date(2026, 11, 23)) is not None
    assert index_skew._cm_rr_for(s, date(2026, 11, 23), exact=True) is None
    # too old -> None
    assert index_skew._cm_rr_for(s, date(2026, 11, 30)) is None


def test_backfill_fills_nulls_and_percentiles() -> None:
    s = _session()
    days = [date(2026, 9, 1 + i) for i in range(25)]
    for i, d in enumerate(days):
        s.add(IndexSkewDaily(date=d))
        _cm(s, d, 0.20 + i * 0.001, 0.15)
    s.flush()
    n = index_skew.backfill_rr_from_cm(s)
    assert n == 25
    rows = s.query(IndexSkewDaily).order_by(IndexSkewDaily.date).all()
    assert all(r.spx_rr_25d_30d is not None for r in rows)
    assert rows[5].spx_rr_pctile_252d is None  # cold (< 20 prior rows)
    assert rows[-1].spx_rr_pctile_252d == 1.0  # rising series -> top of its own history
