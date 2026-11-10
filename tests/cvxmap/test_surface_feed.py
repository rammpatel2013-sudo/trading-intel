"""surface_feed (2026-11-10): per-expiry surface must add up to wide_feed's 0DTE / 1-7 DTE books."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cvx_live as L            # noqa: E402
import surface_feed as SF       # noqa: E402
import wide_feed as W           # noqa: E402
from test_wide_feed import make_rec  # noqa: E402


def _surf(ts="2026-11-06T10:35:00", spot=7400.0):
    rec = make_rec(ts, spot=spot)
    now = datetime.datetime.fromisoformat(ts)
    d = L.decode(rec)["SPX"]
    return d, now, SF.build_surface(W, L, "SPX", d, now.date(), now, SF.BetaTracker())


def test_slices_sum_to_wide_feed_books():
    d, now, s = _surf()
    blk = W.build_slot("SPX", d, now.date(), now)
    assert s["k"] == blk["k"] and s["ex"][0]["dte"] == 0
    for f, wf in (("g", "g"), ("va", "va"), ("ch", "ch")):
        for b, suf in (("oi", ""), ("vol", "v"), ("sig", "t")):
            rows = s[f][b]
            for i in range(len(s["k"])):
                z0 = rows[0][i]
                z7 = sum(r[i] for r in rows[1:])
                assert abs(z0 - blk[wf + suf + "0"][i]) < 1e-4 + 1e-3 * abs(z0)
                assert abs(z7 - blk[wf + suf + "7"][i]) < 1e-4 + 1e-3 * abs(z7)


def test_shape_and_families():
    _d, _now, s = _surf()
    n_e, n_k = len(s["ex"]), len(s["k"])
    assert n_e >= 3 and all(len(r) == n_k for r in s["g"]["oi"])
    assert [e["dte"] for e in s["ex"]] == sorted(e["dte"] for e in s["ex"])
    assert all(e["fam"] == "PM" for e in s["ex"])           # synthetic pull is all .SPXW
    assert s["beta_src"] == "default" and s["beta"] == SF.BETA_DEFAULT


def test_am_family_from_roots():
    am = {7500.0: ({"_sym": ".SPXO261118C7500"}, {"_sym": ".SPXO261118P7500"})}
    mo = {7500.0: ({"_sym": ".SPX261120C7500"}, {"_sym": ".SPX261120P7500"})}
    pm = {7500.0: ({"_sym": ".SPXW261118C7500"}, {"_sym": ".SPXW261118P7500"})}
    assert SF.am_family(am) == "AM" and SF.am_family(mo) == "AM" and SF.am_family(pm) == "PM"


def test_beta_fit_recovers_slope():
    bt = SF.BetaTracker()
    spot, iv = 7500.0, 0.20
    for i in range(40):
        step = 0.1 if i % 2 else -0.07                     # % moves
        spot *= 1 + step / 100
        iv += -1.5 * step / 100                            # -1.5 vol points per +1%
        bt.add("10:%02d" % i, spot, iv)
    b, src = bt.beta()
    assert abs(b + 1.5) < 0.05 and src.startswith("fit")


def test_live_feed_writes_surface_file(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(W, "DATA", str(tmp_path))
    rec = make_rec("2026-11-06T10:35:00")
    monkeypatch.setattr(L, "now_et", lambda: datetime.datetime(2026, 11, 6, 10, 35, 30))
    day = W.Day("20261106", live_mode=True)
    assert day.feed(rec)
    j = json.loads((tmp_path / "surface_live_SPX.json").read_text())
    assert j["t"] == "10:35" and j["sym"] == "SPX" and len(j["g"]["oi"]) == len(j["ex"])
