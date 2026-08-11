"""Unit tests for the pure GEX-transition state machine (no DB)."""

from __future__ import annotations

from trading_intel.market import gex_transition as gt


def _gamma(rows):
    return [{"date": d, "gex_total": g, "spot": sp, "gex_flip": fl, "atm_iv": iv}
            for d, g, sp, fl, iv in rows]


def _iv(rows):
    return [{"ts": d, "tenor_dte": 30, "iv_atm": v} for d, v in rows]


def test_eod_dedupe_last_wins():
    rows = [{"date": "2026-08-10", "gex_total": 100}, {"date": "2026-08-10", "gex_total": 149}]
    eod = gt.eod_gex_series(rows)
    assert len(eod) == 1 and eod[0]["gex"] == 149


def test_classify_quiet_unwind():
    assert gt.classify(-2.0, 0.2) == gt.STATE_QUIET       # fast drop, IV pinned
    assert gt.classify(-2.0, 1.5) == gt.STATE_CONFIRMED   # fast drop, IV up
    assert gt.classify(-2.0, -3.0) == gt.STATE_DROP        # fast drop, IV down (not pinned)
    assert gt.classify(2.0, 0.0) == gt.STATE_REBUILD
    assert gt.classify(0.1, 0.0) == gt.STATE_BASE
    assert gt.classify(None, None) == gt.STATE_BASE


def test_gap_breaks_delta():
    # a >4-day gap must NOT produce a ΔGEX (June-outage guard)
    gamma = _gamma([("2026-06-04", -500, 7553, 7571, 0.17), ("2026-07-28", -139, 7428, 7469, 0.20)])
    res = gt.compute(gamma, [])
    assert res.rows[1].d_gex is None  # gap → no change computed


def test_clean_iv_preferred_over_noisy_gamma_iv():
    gamma = _gamma([("2026-08-08", 140, 7756, 7693, 0.094), ("2026-08-10", 149, 7753, 7698, 0.121)])
    iv = _iv([("2026-08-08", 0.1220), ("2026-08-10", 0.1224)])
    res = gt.compute(gamma, iv)
    last = res.latest
    # clean ΔIV = 12.24 - 12.20 = +0.04 pt, NOT the +2.7pt the noisy gamma IV would give
    assert last.atm_iv is not None and abs(last.atm_iv - 12.24) < 0.01
    assert last.d_iv_pt is not None and abs(last.d_iv_pt - 0.04) < 0.01


def test_latest_state_base_on_flat_tape():
    gamma = _gamma([
        ("2026-08-03", 557, 7600, 7476, 0.10), ("2026-08-04", 383, 7735, 7492, 0.15),
        ("2026-08-05", 347, 7722, 7573, 0.14), ("2026-08-06", 59, 7711, 7657, 0.099),
        ("2026-08-07", 140, 7756, 7692, 0.078), ("2026-08-08", 140, 7756, 7693, 0.094),
        ("2026-08-10", 149, 7753, 7698, 0.121)])
    iv = _iv([("2026-08-03", 0.1252), ("2026-08-04", 0.1353), ("2026-08-05", 0.1296),
              ("2026-08-06", 0.1241), ("2026-08-07", 0.1207), ("2026-08-08", 0.1220),
              ("2026-08-10", 0.1224)])
    res = gt.compute(gamma, iv)
    assert res.latest.state == gt.STATE_BASE
