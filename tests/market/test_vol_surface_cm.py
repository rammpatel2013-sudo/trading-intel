"""Unit tests for the pure constant-maturity vol-surface assembly + read."""

from __future__ import annotations

from trading_intel.market import vol_surface_cm as vs

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


def test_read_rally_confirmed():
    v = vs.build_view(_rows("2026-08-11", 7757, 0.004), _rows("2026-08-04", 7700, 0.0))
    assert v.read_label == "rally-confirmed"


def test_read_rally_unconfirmed():
    v = vs.build_view(_rows("2026-08-11", 7757, -0.002), _rows("2026-08-04", 7700, 0.0))
    assert v.read_label == "rally-unconfirmed"


def test_read_fear_when_down_and_puts_bid():
    now = _rows("2026-08-11", 7600, 0.0)
    # bump put wing IV up
    for r in now:
        if r["side"] == "put":
            r["iv"] += 0.004
    v = vs.build_view(now, _rows("2026-08-04", 7700, 0.0))
    assert v.read_label == "fear"


def test_read_quiet_slide_when_down_and_vol_asleep():
    v = vs.build_view(_rows("2026-08-11", 7600, 0.0), _rows("2026-08-04", 7700, 0.0))
    assert v.read_label == "quiet-slide"


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
