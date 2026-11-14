"""lakha_read (2026-11-13 overnight): golden 2026-10-23 example from the plan + fear release."""
from trading_intel.market import lakha_read as lr


def test_golden_1023_defensive():
    checks = [
        lr.spot_vs_vol([-1.22, -0.52], [1.1, 0.5]),
        lr.base_signal(None, None),
        lr.skew_gate(88),
        lr.iv_percentile(89),
        lr.carry(14.5, 11.1),
        lr.vix_gap(19.0, 14.5),
        lr.term(18.7, 19.0, 20.5),
        lr.long_gamma_proxy(7624, 7718, 0.1),
    ]
    v = lr.verdict(checks)
    # plan's 10/23 example: carry +23% scores +1 here (plan scored 0); term +1 -> -3/8 Defensive
    assert v["band"] == "Defensive" and v["of"] == 8
    assert checks[0]["verdict"].startswith("real selling")
    assert all(c["line"] for c in checks)


def test_divergence_labels():
    assert lr.divergence(0.8, -0.5, 0.3, True)["value"] == "Stall"
    assert lr.divergence(0.8, 0.5, 0.0, False)["value"] == "Breakout"
    assert lr.divergence(-0.8, -0.2, -0.1, False)["value"] == "Exhaustion"


def test_fear_release():
    assert lr.fear_release([0.9, 1.05, 1.06, 1.03, 0.98])["state"] == "release"
    assert lr.fear_release([0.9, 1.05, 1.06, 1.03])["state"] == "in fear"
    assert lr.fear_release([0.9, 0.95, 0.98])["state"] == "early warning"
    r = lr.fear_release([1.1, 1.1, 1.1, 0.9], gex_pct_in_fear=20, spx_off_high_pct=4)
    assert r["strength"] == 2 and "+3.39%" in r["line"]


def test_pct_rank():
    assert lr.pct_rank([1, 2, 3, 4], 3) == 75.0
