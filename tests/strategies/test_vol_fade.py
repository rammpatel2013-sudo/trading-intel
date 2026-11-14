"""vf-v2 spike-fade rule (trading_intel/strategies/vol_fade.py) - constructed paths, no network."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import Signal
from trading_intel.strategies import vol_fade as vf

D0 = date(2026, 1, 5)


def _rows(vix: list[float], ratio: list[float | None], vvix: float | list[float] = 100.0):
    vv = vvix if isinstance(vvix, list) else [vvix] * len(vix)
    return [
        (D0 + timedelta(days=i), v, (v * r) if r is not None else None, vv[i])
        for i, (v, r) in enumerate(zip(vix, ratio, strict=True))
    ]


def _spike(n_before=5, n_after=30, vvix=100.0):
    """calm, one close at 26, VIX9D above VIX for 3 closes, then back under -> fire on day n_before+3."""
    vix = [18.0] * n_before + [26.0, 24.0, 22.0, 20.0] + [19.0] * n_after
    ratio = [0.9] * n_before + [1.05, 1.04, 1.02, 0.97] + [0.92] * n_after
    return _rows(vix, ratio, vvix)


def test_fire_hold_last_day_exit():
    st = vf.evaluate(_spike())
    i_spike, i_fire = 5, 8
    assert "ARMED" in st[i_spike].events and st[i_spike].phase == "ARMED"
    assert st[i_fire].phase == "FIRE" and st[i_fire].events == ["FIRE"]
    assert st[i_fire].fire["strength"] == "normal" and st[i_fire].fire["units"] == 1
    assert st[i_fire + 1].phase == "HOLD" and st[i_fire + 1].trades[-1]["hold_day"] == 1
    assert "LAST_DAY" in st[i_fire + vf.HOLD - 1].events
    last = st[i_fire + vf.HOLD]
    assert "EXIT" in last.events and last.trades[-1]["left"] == 0
    assert (
        st[i_fire + vf.HOLD + 1].phase in ("IDLE", "ARMED") and not st[i_fire + vf.HOLD + 1].trades
    )
    assert sum("FIRE" in s.events for s in st) == 1


@pytest.mark.parametrize(
    ("vvix", "want"), [(110.0, "strong"), (109.99, "normal"), (None, "unknown")]
)
def test_strength_cut(vvix, want):
    st = vf.evaluate(_spike(vvix=vvix))
    assert st[8].fire["strength"] == want


def test_no_spike_no_fire():
    vix = [18.0] * 5 + [24.9, 24.0, 22.0, 20.0] + [19.0] * 5
    ratio = [0.9] * 5 + [1.05, 1.04, 1.02, 0.97] + [0.92] * 5
    st = vf.evaluate(_rows(vix, ratio))
    assert not vf.fire_days(st) and all(s.phase == "IDLE" for s in st)


@pytest.mark.parametrize(("gap", "fires"), [(20, 1), (21, 0)])
def test_window_is_20_sessions(gap, fires):
    # 25+ close on day 0; the release lands `gap` sessions later
    n = gap + 5
    vix = [26.0] + [19.0] * n
    ratio = [0.9] * (gap - 3) + [1.05, 1.05, 1.05, 0.95] + [0.9] * (n + 1 - gap - 1)
    st = vf.evaluate(_rows(vix, ratio))
    assert len(vf.fire_days(st)) == fires
    if fires:
        assert vf.fire_days(st)[0].d == D0 + timedelta(days=gap)


def test_two_releases_in_one_window_both_fire():
    vix = [26.0] + [20.0] * 15
    ratio = [1.05, 1.05, 1.05, 0.95, 1.02, 1.02, 1.02, 0.98] + [0.9] * 8
    st = vf.evaluate(_rows(vix, ratio))
    f = vf.fire_days(st)
    assert [s.d for s in f] == [D0 + timedelta(days=3), D0 + timedelta(days=7)]
    assert len(st[10].trades) == 2 and st[10].phase == "HOLD"


def test_two_closes_above_is_not_a_release():
    vix = [26.0] + [20.0] * 6
    ratio = [1.05, 1.05, 0.95, 0.9, 0.9, 0.9, 0.9]
    assert not vf.fire_days(vf.evaluate(_rows(vix, ratio)))


def test_missing_vix9d_resets_the_run():
    vix = [26.0] + [20.0] * 6
    ratio = [1.05, 1.05, None, 1.05, 0.95, 0.9, 0.9]
    assert not vf.fire_days(vf.evaluate(_rows(vix, ratio)))


def test_fear_returns_flag_once_per_episode():
    vix = [18.0] * 5 + [26.0, 24.0, 22.0, 20.0] + [19.0] * 25
    ratio = [0.9] * 5 + [1.05, 1.04, 1.02, 0.97] + [0.95, 1.01, 1.02, 0.95, 1.03] + [0.9] * 20
    st = vf.evaluate(_rows(vix, ratio))
    flags = [i for i, s in enumerate(st) if "FEAR_RETURNS" in s.events]
    assert flags == [10, 13]
    assert st[11].fear_returns and not st[12].fear_returns


def test_no_look_ahead():
    rows = _spike(n_after=25)
    full = vf.evaluate(rows)
    for k in range(1, len(rows) + 1):
        assert vf.evaluate(rows[:k])[-1].to_dict() == full[k - 1].to_dict()


def test_merge_closes_today_only_when_missing():
    d1, d2 = date(2026, 11, 12), date(2026, 11, 13)
    vix, v9, vv = [(d1, 20.0)], [(d1, 19.0)], [(d1, 100.0)]
    rows = vf.merge_closes(vix, v9, vv, today=(d2, 19.3, 17.8, 101.5))
    assert rows[-1] == (d2, 19.3, 17.8, 101.5)
    rows = vf.merge_closes([*vix, (d2, 19.3)], v9, vv, today=(d2, 99.0, 99.0, 99.0))
    assert rows[-1] == (d2, 19.3, None, None)


def test_headlines_are_plain():
    st = vf.evaluate(_spike())
    assert vf.headline(st[0]).startswith("IDLE")
    assert vf.headline(st[5]).startswith("ARMED")
    assert vf.headline(st[8]).startswith("FIRE") and "SVXY" in vf.headline(st[8])
    assert vf.headline(st[9]).startswith("HOLD")
    assert vf.headline(st[8 + vf.HOLD - 1]).startswith("LAST DAY")
    assert vf.headline(st[8 + vf.HOLD]).startswith("EXIT")


def test_evidence_shape():
    rows = vf.EVIDENCE["rows"]
    assert set(rows) == {"all_20", "strong_20", "normal_20", "all_60"}
    for v in rows.values():
        assert all(len(r) == len(vf.EVIDENCE["cols"]) for r in v)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Signal.__table__.create(engine)
    with Session(engine) as s:
        yield s


def test_emit_is_idempotent(session):
    states = vf.evaluate(_spike())[:9]  # ends on the fire day
    st, new = vf.emit_signals(session, states)
    session.commit()
    assert new == ["FIRE"] and st.phase == "FIRE"
    _, new2 = vf.emit_signals(session, states)
    session.commit()
    assert new2 == []
    rows = session.execute(select(Signal)).scalars().all()
    assert sorted(r.signal_type for r in rows) == ["VOL_FADE_FIRE", "VOL_FADE_STATE"]
    state = next(r for r in rows if r.signal_type == "VOL_FADE_STATE")
    assert state.payload["rule"] == vf.RULE_VERSION and state.payload["pushed"] == ["FIRE"]
    assert state.payload["headline"].startswith("FIRE")
