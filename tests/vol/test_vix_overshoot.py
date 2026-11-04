"""Phase 5 V1 — VIX overshoot maths (pure)."""

from datetime import date, timedelta

from trading_intel.vol import vix_overshoot as vo


def _series(n=200, shock_day=None, shock=0.0):
    d0 = date(2025, 1, 1)
    spx, vix = {}, {}
    p, v = 5000.0, 15.0
    for i in range(n):
        d = d0 + timedelta(days=i)
        r = 0.4 if i % 3 == 0 else -0.3  # deterministic moves, % log
        p *= 2.718281828 ** (r / 100)
        v = v - 1.5 * r  # VIX falls 1.5 pts per +1% SPX
        if i == shock_day:
            v += shock
        spx[d], vix[d] = p, v
    return spx, vix


def test_fit_recovers_beta_and_no_flags_on_clean_data():
    spx, vix = _series()
    rows = vo.compute(vix, spx)
    assert rows, "rows after min_fit"
    assert abs(rows[-1].beta + 1.5) < 1e-6
    assert not any(r.flag for r in rows)


def test_shock_flags_only_that_day_and_no_lookahead():
    spx, vix = _series(shock_day=150, shock=4.0)
    rows = {r.date: r for r in vo.compute(vix, spx)}
    d = date(2025, 1, 1) + timedelta(days=150)
    assert rows[d].flag and rows[d].residual > 3.9
    before = date(2025, 1, 1) + timedelta(days=149)
    assert not rows[before].flag


def test_recovery_counts_flags():
    spx, vix = _series(shock_day=150, shock=4.0)
    rows = vo.compute(vix, spx)
    rec = vo.recovery(rows, spx, vix)
    assert rec["flagged_n"] >= 1
    assert rec["flagged"]["5"]["n"] >= 1
    assert "flag" in vo.plain_read(next(r for r in rows if r.flag)).lower()
