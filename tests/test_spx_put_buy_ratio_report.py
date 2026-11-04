"""spx_put_buy_ratio_report (2026-11-04): ratio, own-history z-score, agreement, plain read."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import spx_put_buy_ratio_report as R  # noqa: E402


def test_build_ratios_z_and_read():
    d0 = date(2026, 10, 1)
    days = {}
    for i in range(25):
        b = 60.0 if i < 24 else 30.0          # last day: buying collapses
        days[d0 + timedelta(days=i)] = {"spot": 6000.0, "rr": 5.0 + (0.5 if i == 24 else 0.0) + 0.01 * (i % 3),
                                        "cvx_prem_buy": b + (i % 3), "cvx_prem_sell": 40.0, "cvx_vol_buy": 1, "cvx_vol_sell": 1,
                                        "tape_buy": 20.0, "tape_sell": 30.0, "tape_n": 4}
    rows = R.build(days)
    last = rows[-1]
    assert abs(last["cvx_prem"] - 30 / 70) < 0.01 and last["z_cvx"] < -1.5
    assert last["agree"] is True                      # both under 50%
    assert "steepened while put buying fell" in R.read(last)
    assert R.read(rows[3]).startswith("Not enough history")
