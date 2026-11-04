"""p1score (2026-11-04): the pre-registered Phase-1 rules."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import p1score as P  # noqa: E402


def _path(xs, t0=571):
    return [(t0 + i, x) for i, x in enumerate(xs)]


def test_level_held_broke_untouched():
    assert P.score_level(_path([7600, 7605, 7609, 7598, 7590]), 7610)["result"] == "held"
    assert P.score_level(_path([7600, 7609, 7616, 7617, 7618]), 7610)["result"] == "broke"
    assert P.score_level(_path([7600, 7601]), 7650)["result"] == "untouched"


def test_lock_needs_ten_steady_columns():
    s = [(572 + 2 * i, 7700 + (0.5 if i % 2 else 0)) for i in range(12)]
    t, lv = P._lock(s)
    assert t == 590 and abs(lv - 7700.25) < 0.3
    assert P._lock([(572 + 2 * i, 7700 + 5 * i) for i in range(12)]) == (None, None)


def test_flip_split_and_crosses():
    xs = [7600 + (3 if i % 2 else 0) for i in range(30)] + [7590 - (9 if i % 2 else 0) for i in range(30)]
    r = P.flip_tests(_path(xs), lambda m: 7595)
    assert r["split"]["ratio"] > 1 and r["cross_down_n"] >= 1


def test_mid_guess_score():
    q = {"10:00": {"7600": [10.0, 10.2, 9.0, 9.2]}, "10:02": {"7600": [10.4, 10.6, 8.7, 8.9]}}
    pr = [{"t": "10:00", "strike": 7600.0, "cp": "C", "side": "buy"},     # call mid up -> buy guess right
          {"t": "10:00", "strike": 7600.0, "cp": "P", "side": "buy"},     # put mid down -> buy guess wrong
          {"t": "11:00", "strike": 7600.0, "cp": "C", "side": "sell"}]    # no quote
    r = P.guess_score(pr, q)
    assert r["n"] == 2 and r["agree"] == 1 and r["no_quote"] == 1
