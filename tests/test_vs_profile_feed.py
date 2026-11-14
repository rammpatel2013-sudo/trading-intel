"""Tests for scripts/vs_profile_feed.py (2026-11-13, VS plan step 2) - pure logic, no DB."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vs_profile_feed as pf  # noqa: E402

SESS = {date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17)}
is_s = SESS.__contains__


def test_ref_session_before_during_after():
    assert pf.ref_session(datetime(2026, 11, 13, 8, 45), is_s) == datetime(2026, 11, 13, 9, 30)
    assert pf.ref_session(datetime(2026, 11, 13, 11, 7, 31), is_s) == datetime(2026, 11, 13, 11, 7)
    # Friday evening -> Monday's open (weekend skipped)
    assert pf.ref_session(datetime(2026, 11, 13, 17, 35), is_s) == datetime(2026, 11, 16, 9, 30)


def _rows():
    # a put pile below 7500 (dealers short), a call pile above 7600 (dealers long), one 0DTE call at 7550
    return {
        (date(2026, 12, 18), 7450.0, "P", False): {"oi": 40_000, "iv": 0.12},
        (date(2026, 12, 18), 7650.0, "C", False): {"oi": 40_000, "iv": 0.12},
        (date(2026, 11, 16), 7550.0, "C", False): {"oi": 60_000, "iv": 0.14},
        (date(2026, 11, 13), 7550.0, "C", False): {"oi": 99_000, "iv": 0.14},   # expired before the ref
        (date(2026, 11, 16), 7500.0, "P", True): {"oi": 50_000, "iv": 0.15},    # AM-settled: gone at the open
    }


def test_ex0_drops_the_expiring_options_and_expired_ones():
    ref = datetime(2026, 11, 16, 9, 30)
    K, _, _, w = pf.book_arrays(_rows(), ref, None)
    assert sorted(K.tolist()) == [7450.0, 7550.0, 7650.0]          # 11/13 expired, the AM 11/16 put settled
    K0, _, _, _ = pf.book_arrays(_rows(), ref, date(2026, 11, 16))
    assert sorted(K0.tolist()) == [7450.0, 7650.0]
    assert w[K.tolist().index(7450.0)] < 0 < w[K.tolist().index(7650.0)]


def test_profile_lines_and_flip():
    ref = datetime(2026, 11, 16, 9, 30)
    grid = np.arange(7300.0, 7801.0, 1.0)
    L = pf.profile_lines(_rows(), 7550.0, ref, grid)
    full, ex0 = L["full"], L["ex0"]
    # the 0DTE call at 7550 makes the full book more long near 7550 than ex-0DTE
    i = int(np.where(grid == 7550.0)[0][0])
    assert full[i] > ex0[i]
    f = pf.flips(grid, ex0, 7550.0)
    assert f["nearest"] is not None and abs(f["nearest"] - 7550) < 15   # equal piles, same IV -> flip near the middle
    assert any(d == "up" for _, d in f["all"])


def test_percentile_and_upsert():
    assert pf.percentile(5.0, [1.0] * 5) is None                    # too short a history
    assert pf.percentile(5.0, list(range(10))) == 60                # 0..5 are <= 5 -> 6/10
    h = pf.upsert([{"date": "2026-11-12", "full": 1}], {"date": "2026-11-13", "full": 2})
    h = pf.upsert(h, {"date": "2026-11-13", "full": 3})
    assert [x["full"] for x in h] == [1, 3]


def test_hedge_rows_match_exact_black_scholes_delta_change():
    """VS step 3: the ES table from the gamma line = the exact BS dealer delta change / 50."""
    from math import erf, log, sqrt

    from vs_flip_score import MULT
    N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))  # noqa: E731
    ref = datetime(2026, 11, 16, 9, 30)
    rows = _rows()
    grid = np.arange(7300.0, 7801.0, 1.0)
    L = pf.profile_lines(rows, 7550.0, ref, grid)
    K, sig, t, w = pf.book_arrays(rows, ref, None)
    cp = [1 if wi > 0 else -1 for wi in w]   # in _rows calls are dealer-long (+), puts dealer-short (-)

    def dealer_delta(S):
        tot = 0.0
        for k, s_, tt, wi, c in zip(K, sig, t, w, cp, strict=True):
            d1 = (log(S / k) + 0.5 * s_ * s_ * tt) / (s_ * sqrt(tt))
            tot += wi * MULT * (N(d1) if c > 0 else N(d1) - 1)
        return tot

    H = {r["move_pct"]: r["es"] for r in pf.hedge_rows(grid, L["full"], 7550.0)}
    for m in (-2.0, -1.0, 1.0, 2.0):
        exact = -(dealer_delta(7550.0 * (1 + m / 100)) - dealer_delta(7550.0)) / 50.0
        assert abs(H[m] - exact) <= max(2.0, 0.01 * abs(exact)), (m, H[m], exact)
