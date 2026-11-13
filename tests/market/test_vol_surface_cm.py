"""Unit tests for the pure constant-maturity vol-surface assembly + read."""

from __future__ import annotations

import math
from statistics import NormalDist

from trading_intel.market import vol_surface_cm as vs

_ND = NormalDist()

RUNGS = [7, 30, 90]
DELTAS = [10.0, 25.0, 50.0]


def _rows(ts, spot, bump_call=0.0):
    out = []
    for r in RUNGS:
        for d in DELTAS:
            base = 0.13 + (0.02 if d < 50 else 0.0)
            out.append(dict(ts=ts, dte=r, delta=d, side="put", iv=base + 0.03 * (1 - d / 50),
                            spot=spot, near_expiry="2026-08-15"))
            out.append(dict(ts=ts, dte=r, delta=d, side="call", iv=base + 0.005 * (1 - d / 50) + bump_call,
                            spot=spot, near_expiry="2026-08-15"))
    return out


def _smile(k, ref=7700.0, atm=0.14, slope=-0.9, curv=1.5):
    m = math.log(k / ref)
    return max(0.05, atm + slope * m + curv * m * m)


def _surface(ts, spot, iv_of_k, rungs=(7, 30, 90), deltas=(10.0, 25.0, 40.0, 50.0)):
    """Delta-grid rows from a smile defined by STRIKE (solve delta -> strike by fixed point)."""
    out = []
    for r in rungs:
        t = r / 365
        for side in ("put", "call"):
            for d in deltas:
                x = _ND.inv_cdf(d / 100)
                sgn = -1 if side == "call" else 1
                iv = 0.14
                for _ in range(60):
                    k = spot * math.exp(sgn * x * iv * math.sqrt(t) + iv * iv * t / 2)
                    iv = iv_of_k(k, spot)
                out.append(dict(ts=ts, dte=r, delta=d, side=side, iv=iv, spot=spot, near_expiry="2026-08-15"))
    return out


def _base(k, spot):
    return _smile(k)


PRIOR = _surface("2026-08-04", 7700, _base)


def test_sticky_strike_down_day_is_quiet_slide_not_fear():
    # Same curve by strike, spot -1.3%: the fixed-DELTA put wing still rises (the old trap)
    now = _surface("2026-08-11", 7600, _base)
    v = vs.build_view(now, PRIOR)
    assert v.iv_chg[(30, 25.0, "put")] > 0.10  # fixed-delta grid shows "put vol bid"
    assert v.read_basis == "fixed-strike"
    assert v.read_label == "quiet-slide"  # nothing was re-priced
    sp = v.split[30]
    assert abs(sp["lift"]) < 0.05 and sp["slide"] > 0.5
    assert sp["regime"] == "along the curve"


def test_curve_lift_on_down_day_is_fear():
    now = _surface("2026-08-11", 7600, lambda k, s: _smile(k) + 0.01)
    v = vs.build_view(now, PRIOR)
    assert v.read_label == "fear"
    assert abs(v.split[30]["lift"] - 1.0) < 0.05
    assert v.split[30]["regime"] == "nervous"


def test_rally_confirmed_when_call_strikes_bid():
    now = _surface("2026-08-11", 7757, lambda k, s: _smile(k) + 0.004)
    assert vs.build_view(now, PRIOR).read_label == "rally-confirmed"


def test_rally_unconfirmed_when_sticky_strike_up():
    v = vs.build_view(_surface("2026-08-11", 7757, _base), PRIOR)
    assert v.read_label == "rally-unconfirmed"
    assert v.split[30]["regime"] == "along the curve" and v.split[30]["slide"] < 0


def test_risk_on_when_put_strikes_crushed_on_rally():
    now = _surface("2026-08-11", 7757, lambda k, s: _smile(k) - (0.02 if k < s else 0.0))
    assert vs.build_view(now, PRIOR).read_label == "risk-on"


def test_parallel_shift_spot_flat_is_all_lift():
    v = vs.build_view(_surface("2026-08-11", 7700, lambda k, s: _smile(k) + 0.01), PRIOR)
    assert abs(v.split[30]["slide"]) < 0.05 and abs(v.split[30]["lift"] - 1.0) < 0.05


def test_broken_rung_is_skipped():
    now = _surface("2026-08-11", 7600, _base)
    for r in now:
        if r["dte"] == 30 and r["side"] == "call":
            r["iv"] += 0.10  # 50Δ call 10 pts above the 50Δ put = broken forward
    v = vs.build_view(now, PRIOR)
    assert v.bad_rungs == [30]
    assert 30 not in v.split and v.read_label == "quiet-slide"
    assert "Skipped broken data: 30d" in v.read_text


def test_split_regime_helper():
    assert vs.split_regime(0.1, 0.1) == "flat"
    assert vs.split_regime(1.0, 0.1) == "along the curve"
    assert vs.split_regime(1.0, 0.8) == "nervous"
    assert vs.split_regime(-1.0, -0.8) == "relief"
    assert vs.split_regime(1.0, -1.0) == "curve moved with price"


def test_no_read_without_prior():
    v = vs.build_view(_rows("2026-08-11", 7757, 0.0), [])
    assert v.read_label == "no-read"


def test_forward_vol_and_atm():
    v = vs.build_view(_rows("2026-08-11", 7757, 0.0), _rows("2026-08-04", 7700, 0.0))
    assert v.rungs == [7, 30, 90]
    assert all(v.atm_now[r] is not None for r in v.rungs)
    assert (7, 30) in v.fwd_now and (30, 90) in v.fwd_now


def test_classify_helper_direct():
    assert vs.classify_read(1.0, 0.5, None, 0.5)[0] == "rally-confirmed"
    assert vs.classify_read(1.0, -0.1, None)[0] == "rally-unconfirmed"
    assert vs.classify_read(-1.0, None, 0.5)[0] == "fear"
    assert vs.classify_read(-1.0, None, 0.0)[0] == "quiet-slide"
    assert vs.classify_read(None, None, None)[0] == "no-read"


def test_classify_skew_shift_terms():
    # up + calls flat but puts collapse → skew-shift risk-on
    assert vs.classify_read(1.0, 0.0, -1.0)[0] == "risk-on"
    # calls bid outright still wins over the skew term
    assert vs.classify_read(1.0, 0.5, -1.0)[0] == "rally-confirmed"
    # down + puts flat but skew steepens (calls sold harder) → fear
    assert vs.classify_read(-1.0, -1.0, 0.0)[0] == "fear"
    # up, mild put relief below threshold → still rally-unconfirmed
    assert vs.classify_read(1.0, 0.0, -0.2)[0] == "rally-unconfirmed"
