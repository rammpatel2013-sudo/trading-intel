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


def test_signed_weights_override_the_naive_sign():
    """VS step 4: a call the tape says customers BOUGHT is dealer-short - the signed line flips its sign."""
    ref = datetime(2026, 11, 16, 9, 30)
    rows = {(date(2026, 12, 18), 7550.0, "C", False): {"oi": 10_000, "iv": 0.13}}
    grid = np.array([7550.0])
    naive = pf.profile_lines(rows, 7550.0, ref, grid)["full"][0]
    signed = pf.signed_line(rows, {(date(2026, 12, 18), 7550.0, "C", False): -10_000.0}, ref, grid)[0]
    assert naive > 0 and abs(signed + naive) < 1e-9


def test_last_trade_day():
    # Friday evening, reading Monday: Friday's prints. Monday morning: Friday's prints too.
    assert pf.last_trade_day(datetime(2026, 11, 16, 9, 30), datetime(2026, 11, 13, 17, 35), is_s) == date(2026, 11, 13)
    assert pf.last_trade_day(datetime(2026, 11, 16, 9, 30), datetime(2026, 11, 16, 8, 45), is_s) == date(2026, 11, 13)
    assert pf.last_trade_day(datetime(2026, 11, 17, 9, 30), datetime(2026, 11, 17, 8, 45), is_s) == date(2026, 11, 16)


class _FakeDb:
    """Two stored sessions (Thu 11/12, Fri 11/13), no tape."""

    def __init__(self):
        self.b = [(datetime(2026, 11, 12, 8, 35), "am"), (datetime(2026, 11, 12, 17, 15), "eod"),
                  (datetime(2026, 11, 13, 8, 35), "am"), (datetime(2026, 11, 13, 17, 15), "eod")]

    def books(self):
        return self.b

    def days(self):
        return sorted({t.date() for t, _ in self.b})

    def book_on(self, d, kind):
        c = [t for t, k in self.b if t.date() == d and k == kind]
        return min(c) if c else None

    def rows(self, ts, kind):
        return _rows()

    def spot(self, at):
        return 7550.0

    def tape(self, d):
        return []


def test_build_end_to_end_with_a_fake_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "HIST", tmp_path / "hist.json")
    out = pf.build(_FakeDb(), datetime(2026, 11, 13, 17, 35))
    assert out["ok"] and out["session"] == "2026-11-16" and out["book"]["kind"] == "eod"
    assert len(out["full"]) == out["grid"]["n"] == len(out["ex0"]) and len(out["hedge"]) == 6
    assert out["signed"]["ok"] is False and "no SPX index tape" in out["signed"]["reason"]
    assert out["pct"]["full"] is None and out["hist_n"] == 2       # backfill gave 2 past sessions (< 10 -> no percentile)


def test_charm_matches_a_finite_difference_of_dealer_delta():
    """VS step 5: charm_bn = (dealer delta tomorrow - today) at a fixed price, in $bn/day."""
    from math import erf, log, sqrt
    N = lambda x: 0.5 * (1 + erf(x / sqrt(2)))  # noqa: E731
    K, sig, t, w = np.array([7600.0]), np.array([0.14]), np.array([20 / 365]), np.array([10_000.0])
    S = 7550.0
    c = pf.charm_bn(K, sig, t, w, np.array([S]))[0]
    d = lambda tt: N((log(S / 7600.0) + 0.5 * 0.14 ** 2 * tt) / (0.14 * sqrt(tt)))  # noqa: E731
    h = 0.01  # day
    fd = (d(t[0] - h / 365) - d(t[0])) / h * 10_000 * 100 * S / 1e9
    assert abs(c - fd) < 0.005 * abs(fd)


def test_red_flag_and_buckets():
    grid = np.arange(7500.0, 7601.0, 1.0)
    calm = np.full(len(grid), 1.0)
    mixed = np.where((grid > 7540) & (grid < 7560), -1.0, 1.0)
    assert pf.red_flag(grid, calm, 7550.0, 40.0)["flag"] is False
    rf = pf.red_flag(grid, mixed, 7550.0, 40.0)
    assert rf["flag"] is True and rf["changes"] == 2 and rf["band"] == [7530.0, 7570.0]
    names = [n for n, _ in pf.expiry_buckets(_rows(), datetime(2026, 11, 16, 9, 30))]
    assert names == ["0DTE", "next (Fri 12/18)"]   # 11/13 expired; 12/18 is the next expiry


def test_charm_view_shape():
    v = pf.charm_view(_rows(), datetime(2026, 11, 16, 9, 30), np.arange(7400.0, 7701.0, 1.0), 7550.0)
    assert list(v["lines"]) == ["0DTE", "next (Fri 12/18)"] and len(v["total"]) == 301
    assert [c["t"] for c in v["clock"]][:2] == ["09:30", "10:00"] and v["clock"][-1]["t"] == "15:00" and v["straddle"] > 0
