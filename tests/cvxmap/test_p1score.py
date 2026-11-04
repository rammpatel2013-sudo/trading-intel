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


def test_proj_score_per_model_and_running():
    proj = {"10:30": {"plain": {"S": 7684.5, "gz": 7607.3, "gpk": 7705, "nz": 1},
                      "smile": {"S": 7684.5, "gz": 7690.0, "gpk": 7700, "nz": 1},
                      "vclock": None, "both": {"S": 7684.5, "gz": None, "gpk": 7702.5, "nz": 0}},
            "12:00": {"err": "boom"}}
    r = P.proj_score(proj, 7700.0)
    assert len(r) == 1 and r[0]["t"] == "10:30"
    assert abs(r[0]["naive"] - 15.5) < 1e-9
    assert abs(r[0]["plain_gz_err"] - 92.7) < 1e-6 and r[0]["smile_gz_err"] == 10.0
    assert r[0]["both_gz_err"] is None and "vclock_gz_err" not in r[0]
    R = P.running([("2026-11-05", {"proj": r}), ("2026-11-06", {"proj": [dict(r[0], smile_gz_err=20.0)]})])
    assert R["proj"]["10:30"]["n"] == 2 and abs(R["proj"]["10:30"]["smile_gz"] - 15.0) < 1e-9
    assert P.proj_score(None, 7700.0) == [] and P.proj_score(proj, None) == []


def test_basis_score_and_running():
    path = _path([7680, 7683, 7686, 7688, 7700, 7701, 7702], t0=630)
    bs = {"10:30": {"oiv": {"S": 7680, "flips": 0, "cz_now": 7700.0, "cz": 7640.0, "gz": 7676.0},
                    "ois": {"S": 7680, "flips": 1, "cz_now": None, "cz": None, "gz": 7690.0},
                    "led": {"na": "no ledger for today"}},
          "12:00": {"err": "x"}}
    r = P.basis_score(bs, 7700.0, path)
    assert len(r) == 1 and r[0]["oiv_flips"] == 0 and r[0]["oiv_cz_err"] == 60.0 and r[0]["oiv_gz_err"] == 24.0
    assert r[0]["oiv_cz_res"] in ("touched", "held", "broke") and r[0]["ois_cz_err"] is None and "led_flips" not in r[0]
    R = P.running([("2026-11-05", {"basis": r})])
    assert R["basis"]["oiv"]["n"] == 1 and R["basis"]["ois"]["flips"] == 1 and "led" not in R["basis"]
