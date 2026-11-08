"""Week-book identity (candidate 2026-11-07/08): AM-settled SPX monthlies and PM-settled SPXW are different contracts —
the book keeps both, times each to its own endpoint (exchange calendar), never depends on row order, and shows
conflicting duplicates instead of hiding them."""
import random
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import am_feed as af          # noqa: E402
import cvx_live as cl         # noqa: E402
import market_cal as mc       # noqa: E402

D = date(2026, 11, 20)


def _rows():
    # (source, expiry, dte, strike, cp, oi, iv, delta) — real 11/06 values for 11/20 7400 / 7500
    return [("convex_am", D, 7, 7400.0, "P", 2950, 0.2256, -0.3), ("convex_am_set", D, 7, 7400.0, "P", 23799, 0.2242, -0.3),
            ("convex_am", D, 7, 7400.0, "C", 227, 0.2255, 0.6), ("convex_am_set", D, 7, 7400.0, "C", 7108, 0.2243, 0.6),
            ("convex_am", D, 7, 7500.0, "P", 8909, 0.2046, -0.5), ("convex_am_set", D, 7, 7500.0, "P", 41363, 0.2033, -0.5),
            ("convex_am", date(2026, 11, 13), 1, 7450.0, "C", 500, 0.21, 0.4)]


def _sig(out):
    return [(r["e"], r["set"], r["k"], r["cOI"], r["pOI"], r["cIV"], r["pIV"], r["exp_t"]) for r in out]


def _shuffled(rows, s):
    rr = list(rows)
    random.Random(s).shuffle(rr)
    return rr


def test_am_and_pm_stay_separate_rows_with_their_own_endpoints():
    out, rc = af._near_rows(_rows(), 7)
    k74 = [r for r in out if r["k"] == 7400.0]
    assert {(r["set"], r["pOI"], r["cOI"]) for r in k74} == {("PM", 2950, 227), ("AM", 23799, 7108)}
    assert {r["set"]: r["exp_t"] for r in k74} == {"PM": "2026-11-20T16:00:00-05:00", "AM": "2026-11-20T09:30:00-05:00"}
    assert rc["n_conflicts"] == 0 and rc["repeats"] == 0


def test_reconciles_contracts_and_oi_per_family():
    rows = _rows()
    _, rc = af._near_rows(rows, 7)
    for src, fam in (("convex_am", "PM"), ("convex_am_set", "AM")):
        f = rc["families"][fam]
        mine = [r for r in rows if r[0] == src]
        assert f["in_rows"] == f["contracts"] == f["out_contracts"] == len(mine)
        assert f["oi"] == f["out_oi"] == sum(r[5] for r in mine)


def test_row_order_does_not_change_the_book():
    ref = _sig(af._near_rows(_rows(), 7)[0])
    for s in range(25):
        assert _sig(af._near_rows(_shuffled(_rows(), s), 7)[0]) == ref


def test_equal_oi_conflicting_iv_is_order_independent_and_reported():
    # the reviewer's case: same contract, same OI, IV 20% vs 30%
    rows = _rows() + [("convex_am_set", D, 7, 7600.0, "C", 500, 0.20, 0.2), ("convex_am_set", D, 7, 7600.0, "C", 500, 0.30, 0.2)]
    kept = set()
    for s in range(30):
        out, rc = af._near_rows(_shuffled(rows, s), 7)
        r = [x for x in out if x["k"] == 7600.0][0]
        kept.add((r["cOI"], r["cIV"]))
        assert rc["n_conflicts"] == 1
        assert sorted(v["iv"] for v in rc["conflicts"][0]["values"]) == [0.20, 0.30]
    assert kept == {(500, 0.20)}                      # fixed rule: larger OI, then lower IV


def test_larger_oi_wins_and_identical_repeats_are_not_conflicts():
    rows = _rows() + [("convex_am_set", D, 7, 7400.0, "P", 100, 0.30, -0.3),        # conflicting, smaller OI
                      ("convex_am", D, 7, 7500.0, "P", 8909, 0.2046, -0.5)]          # identical repeat
    for s in range(10):
        out, rc = af._near_rows(_shuffled(rows, s), 7)
        am = [r for r in out if r["k"] == 7400.0 and r["set"] == "AM"][0]
        assert (am["pOI"], am["pIV"]) == (23799, 0.2242)
        assert rc["n_conflicts"] == 1 and rc["repeats"] == 1


def test_unknown_source_and_window_are_excluded():
    rows = _rows() + [("other_feed", D, 7, 7400.0, "P", 999, 0.2, -0.3), ("convex_am", D, 9, 7600.0, "P", 50, 0.2, -0.2)]
    out, _ = af._near_rows(rows, 7)
    assert sum(r["pOI"] + r["cOI"] for r in out) == sum(r[5] for r in _rows())


def test_am_monthly_kept_on_its_own_morning_pm_same_day_not():
    rows = [("convex_am_set", D, 0, 7400.0, "P", 10, 0.2, -0.3), ("convex_am", D, 0, 7400.0, "P", 20, 0.2, -0.3)]
    out, _ = af._near_rows(rows, 7)
    assert [(r["set"], r["pOI"]) for r in out] == [("AM", 10)]


def test_calendar_endpoints_and_session():
    assert mc.model_endpoint(date(2026, 11, 27), "PM") == "2026-11-27T13:00:00-05:00"      # early close
    assert mc.model_endpoint(date(2026, 12, 24), "PM") == "2026-12-24T13:00:00-05:00"
    assert mc.model_endpoint(date(2027, 3, 19), "PM") == "2027-03-19T16:00:00-04:00"       # EDT
    assert not mc.is_session(date(2026, 11, 26)) and mc.is_session(date(2026, 11, 27))
    s = af._session_info("2026-11-27")
    assert s["early_close"] and s["close"] == "13:00" and s["calendar_verified"]
    assert not mc.verified(date(2028, 1, 3))


def test_lean_keeps_family_apart():
    cur = [{"expiry": D, "set": "AM", "strike": 7400.0, "cp": "C", "dte": 7, "oi": 7108, "oi_change": 1000, "iv": 0.23, "delta": 0.5},
           {"expiry": D, "set": "PM", "strike": 7400.0, "cp": "C", "dte": 7, "oi": 227, "oi_change": 50, "iv": 0.20, "delta": 0.5}]
    prev = {(D, 7400.0, "C", "AM"): 0.22, (D, 7400.0, "C", "PM"): 0.20}
    out = af.lean_from_rows(cur, prev, 7400.0)
    by = {(r["set"], r["oi_change"]): r["d_iv_rel"] for r in out["top"]}
    assert by[("AM", 1000)] == 0.0 and by[("PM", 50)] == 0.0      # each ATM vs its own family


def test_near_book_uses_newest_eligible_snapshot():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from trading_intel.memory.models import OiChainEod
    eng = create_engine("sqlite://")
    OiChainEod.__table__.create(eng)
    t_old, t_new = datetime(2026, 11, 13), datetime(2026, 11, 14)
    with Session(eng) as s:
        for src, e, dte, k, cp, oi, iv, d in _rows():
            s.add(OiChainEod(symbol="SPX", ts=t_old, source=src, expiry=e, strike=k, cp=cp, dte=dte, oi=oi, iv=iv, delta=d))
        s.add(OiChainEod(symbol="SPX", ts=t_new, source="some_other_job", expiry=D, strike=7400.0, cp="P", dte=6, oi=1, iv=0.2))
        s.commit()
        nb = af.near_book(s, "SPX")
    assert nb["as_of_date"] == "2026-11-13" and len(nb["rows"]) == 5          # not the newer, non-eligible snapshot


def _book(sym_pairs):
    return {k: ({"_sym": cs, "oi": 1}, {"_sym": ps, "oi": 1}) for k, (cs, ps) in sym_pairs.items()}


def _d(groups):
    exps = [(h, b) for h, b in groups]
    kinds = [cl._group_kind("SPX", b) for _h, b in exps]
    return {"spot": 7400.0, "exps": exps, "kind": kinds, "pm_idx": cl._pm_index(exps, kinds)}


H19, H20 = 20776, 20777      # 2026-11-19, 2026-11-20 (days since 1970)


def test_feed_groups_am_listed_first_is_not_taken_as_spxw():
    am = _book({7400.0: (".SPX261120C7400", ".SPX261120P7400")})
    pm = _book({7400.0: (".SPXW261120C7400", ".SPXW261120P7400")})
    d19 = _book({7400.0: (".SPXW261119C7400", ".SPXW261119P7400")})
    d = _d([(H19, d19), (H20, am), (H20, pm)])
    assert [cl.exp_date(h) for h, b in cl.pm_groups(d)] == ["2026-11-19", "2026-11-20"]
    assert cl.pm_groups(d)[1][1] is pm and cl.am_groups(d)[0][1] is am and cl.group_issues(d) == []


def test_feed_groups_missing_mixed_unknown():
    am = _book({7400.0: (".SPX261120C7400", ".SPX261120P7400")})
    mixed = _book({7400.0: (".SPXW261120C7400", ".SPX261120P7400")})
    unk1, unk2 = _book({7400.0: ("", "")}), _book({7400.0: (None, None)})
    d = _d([(H20, unk1)])                                  # one unknown group alone on its date -> used, flagged
    assert len(cl.pm_groups(d)) == 1 and cl.group_issues(d) == ["2026-11-20 group unknown"]
    d = _d([(H20, unk1), (H20, unk2)])                     # two unknown on one date -> last listed, flagged
    assert cl.pm_groups(d)[0][1] is unk2 and len(cl.group_issues(d)) == 2
    d = _d([(H20, am), (H20, mixed)])                      # mixed never used
    assert cl.pm_groups(d) == [] and cl.group_issues(d) == ["2026-11-20 group mixed"]
    d = _d([(H20, am), (H20, unk1)])                       # unknown next to a known AM group -> not used
    assert cl.pm_groups(d) == []
    assert cl._group_kind("SPY", unk1) == "PM"             # only SPX has an AM root
