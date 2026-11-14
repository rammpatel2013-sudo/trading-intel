# ruff: noqa: N812  (house style: L / W / V / X / T module aliases)
"""vol_tells (2026-11-14): calm drop / rally + fear up day tells and the VIX futures basis - pure,
synthetic data."""

import math
from datetime import date, timedelta

import pytest

from trading_intel.vol import vol_tells as T

D0 = date(2026, 1, 5)


def _series(n: int, tail: list[tuple[float, float]] | None = None) -> tuple[dict, dict]:
    """n sessions where dVIX = -1.5 * r (r = SPX % move, alternating +-0.6%), then ``tail`` =
    [(spx % move, vix point change)] appended."""
    vix, spx = {}, {}
    v, s = 18.0, 6000.0
    d = D0
    moves = [(0.6 if i % 2 else -0.6, None) for i in range(n)] + list(tail or [])
    for r, dv in moves:
        d += timedelta(days=1)
        s *= math.exp(r / 100.0)
        v += -1.5 * r if dv is None else dv
        vix[d], spx[d] = v, s
    return vix, spx


def test_setups_need_history():
    vix, spx = _series(30)
    assert T.setups(vix, spx)["found"] is False


def test_calm_drop_fires_and_stays_lit_for_five_sessions():
    vix, spx = _series(100, [(-1.5, 0.5)])  # model wants +2.25, VIX rose 0.5 -> residual -1.75
    out = T.setups(vix, spx)
    c = out["calm"]["last"]
    assert c["active"] and c["ago"] == 0 and c["spx"] == pytest.approx(-1.5)
    assert c["res"] == pytest.approx(-1.75, abs=0.01)
    assert out["rally"]["last"] is None
    assert out["calm"]["name"] == "Calm drop" and out["calm"]["ev"]["n"] == 197
    vix, spx = _series(100, [(-1.5, 0.5)] + [(0.1, -0.15)] * 4)
    assert T.setups(vix, spx)["calm"]["last"]["active"]  # 4 sessions later: still lit
    vix, spx = _series(100, [(-1.5, 0.5)] + [(0.1, -0.15)] * 5)
    last = T.setups(vix, spx)["calm"]["last"]
    assert last["ago"] == 5 and not last["active"]


def test_a_normal_down_day_is_not_calm():
    vix, spx = _series(100, [(-1.5, 2.25)])  # VIX did what the model said
    assert T.setups(vix, spx)["calm"]["last"] is None
    vix, spx = _series(100, [(-0.8, 0.0)])  # residual -1.2 but SPX only -0.8%
    assert T.setups(vix, spx)["calm"]["last"] is None


def test_rally_fear_up_and_provisional_flag():
    vix, spx = _series(100, [(1.2, 0.3)])
    last_day = max(vix)
    out = T.setups(vix, spx, provisional=last_day)
    r = out["rally"]["last"]
    assert r["active"] and r["vix_chg"] == pytest.approx(0.3)
    assert out["provisional"] is True and out["asof"] == last_day.isoformat()
    vix, spx = _series(100, [(1.2, -0.1)])  # VIX down on the rally = normal
    assert T.setups(vix, spx, provisional=None)["rally"]["last"] is None


def _exps(asof: date, f1: float, f2: float, extra: list[dict] | None = None) -> list[dict]:
    return [
        {"e": "2026-11-18", "F": f1},
        {"e": "2026-12-16", "F": f2},
        {"e": "2026-11-20", "F": 99.0},
    ] + (
        extra or []
    )  # 11/20 = a weekly: ignored


def test_basis_matches_the_1113_research_row():
    # 2026-11-13: Nov VX 19.1079 (5 days), Dec VX 19.4379 (33 days), VIX 19.30 -> VX30 19.4025, +0.10
    b = T.vx_basis(_exps(date(2026, 11, 13), 19.1079, 19.4379), date(2026, 11, 13), 19.30)
    assert b["found"] and b["vx30"] == pytest.approx(19.40, abs=0.005)
    assert b["basis"] == pytest.approx(0.10, abs=0.005)
    assert b["state"] == "normal" and b["label"] == "Normal"
    assert [x["t"] for x in b["legs"]] == [5, 33]


def test_basis_states_and_clamp():
    asof = date(2026, 11, 13)
    assert T.vx_basis(_exps(asof, 19.0, 19.2), asof, 19.6)["state"] == "cheap"
    cr = T.vx_basis(_exps(asof, 19.0, 21.0), asof, 17.0)
    assert cr["state"] == "crowded" and cr["label"] == "Crowded & expensive"
    # on the expiry day the expiring contract is gone: Dec (33 -> 28 days) and Jan
    asof = date(2026, 11, 18)
    b = T.vx_basis(_exps(asof, 19.0, 20.0, [{"e": "2027-01-20", "F": 21.0}]), asof, 18.0)
    assert [x["e"] for x in b["legs"]] == ["2026-12-16", "2027-01-20"]
    # front 35 days out -> weight clamped to 0 (VX30 = the front)
    asof = date(2026, 11, 11)
    b = T.vx_basis([{"e": "2026-12-16", "F": 20.0}, {"e": "2027-01-20", "F": 22.0}], asof, 18.0)
    assert b["w2"] == 0.0 and b["vx30"] == 20.0


def test_basis_not_found():
    asof = date(2026, 11, 13)
    assert T.vx_basis([{"e": "2026-11-18", "F": 19.0}], asof, 19.0)["found"] is False
    assert T.vx_basis(_exps(asof, 19.0, 19.5), asof, 0.0)["found"] is False
    assert T.vx_basis([{"e": "bad"}, {"F": 3}], asof, 19.0)["found"] is False
