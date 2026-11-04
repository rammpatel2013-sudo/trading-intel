"""gapread (2026-11-04): ATM straddle by parity, straddle IV inversion, gap summary."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import gapread  # noqa: E402


def _px(F, K, T, s):
    return gapread._straddle_px(F, K, T, s)


def test_straddle_iv_round_trip():
    T = gapread.years_to_settle("2026-11-04T09:30:03", "2026-11-04")
    px = _px(7600.0, 7600.0, T, 0.15)
    assert abs(gapread.straddle_iv(px, 7600.0, 7600.0, T) - 0.15) < 1e-4


def test_atm_by_parity_not_by_stale_spot():
    # 09:30 pull: index spot still says the prior close (7568), the options say ~7600
    T = gapread.years_to_settle("2026-11-04T09:30:03", "2026-11-04")
    book = {}
    for k in range(7500, 7705, 5):
        c = gapread._straddle_px(7600.0, k, T, 0.14) - max(k - 7600.0, 0) - max(7600.0 - k, 0)
        cm = max(7600.0 - k, 0) + c / 2
        pm = max(k - 7600.0, 0) + c / 2
        book[float(k)] = ({"bid_price": cm - 0.1, "ask_price": cm + 0.1}, {"bid_price": pm - 0.1, "ask_price": pm + 0.1})
    r = gapread.atm_straddle(book, "2026-11-04T09:30:03", "2026-11-04", near=7568.0)
    assert r["k"] == 7600.0
    assert abs(r["F"] - 7600.0) < 0.5


def test_one_sided_quotes_skipped():
    book = {7600.0: ({"bid_price": 0, "ask_price": 10}, {"bid_price": 9, "ask_price": 11})}
    assert gapread.atm_straddle(book, "2026-11-04T09:30:03", "2026-11-04") is None


def test_summary_and_bucket():
    g = {"prev_close": 7568.47, "on": {"straddle": 40.0, "iv": 0.12}, "am": {"straddle": 36.0, "iv": 0.13}}
    s = gapread.summarize(g, 7588.47, close_px=7608.47, high=7620.0, low=7580.0)
    assert s["gap"] == 20.0 and s["gap_pct_on"] == 50.0
    assert s["am_on_ratio"] == 0.9 and s["iv_chg"] == 1.0
    assert math.isclose(s["used_pct"], 55.6, abs_tol=0.05)
    assert gapread.gap_bucket(s["gap_pct_on"]).startswith("medium")
    assert gapread.gap_bucket(None) == "no data"
