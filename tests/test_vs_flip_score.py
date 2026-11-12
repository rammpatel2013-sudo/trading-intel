# ruff: noqa: N806
"""Pure-logic tests for scripts/vs_flip_score.py (created 2026-11-10)."""
from __future__ import annotations

from datetime import date, datetime

import numpy as np

from trading_intel.scheduler.jobs._script_report import load_script

vs = load_script("vs_flip_score")


def test_monthly_and_quarterly_dates() -> None:
    assert vs.is_monthly_or_quarterly(date(2026, 11, 20))      # 3rd Friday
    assert vs.is_monthly_or_quarterly(date(2026, 12, 31))      # quarter-end weekday
    assert vs.is_monthly_or_quarterly(date(2026, 9, 30))
    assert not vs.is_monthly_or_quarterly(date(2026, 11, 13))  # 2nd Friday
    assert not vs.is_monthly_or_quarterly(date(2026, 10, 30))  # month-end, not quarter-end


def test_years_left_floor_and_am_settle() -> None:
    ref = datetime(2026, 11, 18, 9, 0)
    am = vs.expiry_dt(date(2026, 11, 18), True)
    assert am.hour == 9 and am.minute == 30
    assert abs(vs.years_left(am, ref) - 3600 / vs.YEAR_S) < 1e-12   # 30 min -> 1 h floor
    pm = vs.expiry_dt(date(2026, 11, 18), False)
    assert abs(vs.years_left(pm, ref) - 7 * 3600 / vs.YEAR_S) < 1e-12


def test_doi_div_weight_signs_new_contracts_by_iv() -> None:
    # call adds with IV down = customers sold = dealers long
    assert vs.doi_div_weight("C", 100, 150, -0.01) == 150
    # call adds with IV up = customers bought = dealers short
    assert vs.doi_div_weight("C", 100, 150, 0.01) == 50
    # put adds with IV down = dealers long on the new 50
    assert vs.doi_div_weight("P", 100, 150, -0.01) == -50
    # no IV change -> naive
    assert vs.doi_div_weight("P", 100, 150, None) == -150
    # closes keep the naive sign on what is left
    assert vs.doi_div_weight("C", 100, 60, 0.02) == 60


def test_ledger_step_adds_and_proportional_closes() -> None:
    pos = vs.ledger_step(0.0, "P", 0, 1000, -0.02)     # put sold to open -> dealers long
    assert pos == 1000
    pos = vs.ledger_step(pos, "P", 1000, 500, 0.01)    # half closed
    assert pos == 500
    assert vs.ledger_step(pos, "P", 500, 500, 0.0) == 500


def test_crossings_direction_and_nearest() -> None:
    grid = np.arange(7500.0, 7701.0, 1.0)
    tot = np.where(grid < 7594, -1.0, 1.0) * np.abs(grid - 7594) + np.where(grid > 7680, -(grid - 7680) * 6, 0)
    cr = vs.crossings(grid, tot)
    ups = [x for x, d in cr if d == "up"]
    downs = [x for x, d in cr if d == "down"]
    assert any(abs(x - 7594) <= 1 for x in ups)
    assert downs and all(x > 7680 for x in downs)
    assert abs(vs.nearest(cr, 7600, "up") - 7594) <= 1
    assert vs.nearest(cr, 7600, "sideways") is None


def test_sign_and_verdict() -> None:
    assert vs.sign_ok("+", 2.0) and not vs.sign_ok("+", -0.1)
    assert vs.sign_ok("<=0", 0.2) and not vs.sign_ok("<=0", 1.0)
    v = vs.verdict([5.0, 8.0, 30.0])
    assert v["n"] == 3 and v["within_10"] == 2 and not v["pass"]
    v = vs.verdict([5.0, 8.0, 9.0, None])
    assert v["within_10"] == 3 and v["share"] == 0.75 and v["pass"]
    assert vs.verdict([])["pass"] is False


def test_gamma_profile_naive_flip_between_put_and_call_walls() -> None:
    # one big put line below, one big call line above, same size: flip sits between them
    K = np.array([7500.0, 7700.0])
    sig = np.array([0.15, 0.15])
    t = np.array([10 / 365, 10 / 365])
    w = np.array([-10000.0, 10000.0])
    grid = np.arange(7400.0, 7801.0, 1.0)
    tot = vs.gamma_profile(K, sig, t, w, grid)
    cr = vs.crossings(grid, tot)
    up = vs.nearest(cr, 7600, "up")
    assert up is not None and abs(up - 7600) < 15
    assert tot[grid.tolist().index(7500.0)] < 0 < tot[grid.tolist().index(7700.0)]
