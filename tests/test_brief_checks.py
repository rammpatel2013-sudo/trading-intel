"""Brief self-audit — each check fires on the fault it was written for."""
from datetime import date

from trading_intel.synthesis.brief_checks import run_checks, summary_line
from trading_intel.synthesis.daily_brief import _sanitize_levels

TODAY = date(2026, 10, 22)


def _ctx(**over):
    base = {
        "session_date": "2026-10-21",
        "indices": [{"symbol": "SPX", "spot": 7690.0, "flip": 7743.0, "asof": "2026-10-21"}],
        "doc": {"spot": 7690.0, "put_wall": 7650.0, "call_wall": 7800.0},
        "zero_dte": {"spot": 7690.0, "put_wall": 7680.0, "call_wall": 7750.0},
        "pre_open_spot": {"src": "spy", "spot": 7690.0, "chain_spot": 7695.0, "gap_pct": 0.07},
        "market_read": {"mechanics": {"gex_flip": 7745.0}},
        "newsletter": {"sources": {}},
        "vix": {"asof": "2026-10-21"},
        "filtered_levels": [],
    }
    base.update(over)
    return base


def _by(res):
    return {r["name"]: r for r in res}


def test_all_pass_on_a_clean_morning():
    res = run_checks(None, _ctx(), today=TODAY)
    assert all(r["status"] == "ok" for r in res), _by(res)
    assert summary_line(res) == "data checks 7/7 pass"


def test_2026_10_22_faults_are_caught():
    # the 8,000/8,000 walls, the stale spot, the hallucinated Doc level
    res = _by(run_checks(None, _ctx(
        doc={"spot": 7758.0, "put_wall": 8000.0, "call_wall": 8000.0},
        indices=[{"symbol": "SPX", "spot": 7758.0, "flip": 7745.0, "asof": "2026-10-21"}],
        pre_open_spot=None,
        filtered_levels=[{"source": "DOC", "name": "gamma_flip", "value": 10}] * 4,
    ), today=TODAY))
    assert res["walls"]["status"] == "fail" and "wrong side" in res["walls"]["detail"]
    assert res["spot"]["status"] == "fail" and "yesterday" in res["spot"]["detail"]
    assert res["levels"]["status"] == "warn" and "DOC" in res["levels"]["detail"]


def test_flip_gap_and_stated_disagreement():
    res = _by(run_checks(None, _ctx(
        market_read={"mechanics": {"gex_flip": 7800.0}},
        newsletter={"sources": {"DOC": {"as_of": "2026-10-22", "levels": [{"name": "gamma_flip", "value": 7600}]}}},
    ), today=TODAY))
    assert res["flip"]["status"] == "fail"
    assert res["stated"]["status"] == "warn" and "DOC flip 7,600" in res["stated"]["detail"]


def test_sanitizer_reports_what_it_drops():
    dropped = []
    ns = {"sources": {"DOC": {"levels": [{"name": "put_wall", "value": 19, "note": "VIX was at 19"}, {"name": "key_support", "value": 7600}]}}}
    out = _sanitize_levels(ns, 7660.0, dropped)
    assert [lv["value"] for lv in out["sources"]["DOC"]["levels"]] == [7600]
    assert dropped == [{"source": "DOC", "name": "put_wall", "value": 19, "note": "VIX was at 19"}]
