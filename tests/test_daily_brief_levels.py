"""Daily-brief level hygiene: extractor mis-tags are dropped, pre-open spot re-marks the board."""
from datetime import date

from trading_intel.synthesis.daily_brief import _apply_pre_open_spot, _sanitize_levels


def test_sanitize_drops_mis_tagged_price_levels():
    ns = {"sources": {"DOC": {"as_of": "2026-09-21", "levels": [
        {"name": "gamma_flip", "value": 10},        # "gamma is expected to flip"
        {"name": "put_wall", "value": 19},          # "VIX was at 19"
        {"name": "key_resistance", "value": 7700},
        {"name": "expected_move", "value": 200, "unit": "points"},  # not a price level
    ]}, "NORSEMAN": {"levels": [
        {"name": "key_resistance", "value": 4000},  # Huginn fund, not SPX
        {"name": "bull_bear_line", "value": -10},
    ]}}}
    out = _sanitize_levels(ns, 7660.0)
    doc = [(lv["name"], lv["value"]) for lv in out["sources"]["DOC"]["levels"]]
    assert doc == [("key_resistance", 7700), ("expected_move", 200)]
    assert out["sources"]["NORSEMAN"]["levels"] == []
    assert out["sources"]["DOC"]["as_of"] == "2026-09-21"


def test_sanitize_is_noop_without_spot():
    ns = {"sources": {"DOC": {"levels": [{"name": "gamma_flip", "value": 10}]}}}
    assert _sanitize_levels(ns, None) is ns


def test_pre_open_spot_remarks_yesterdays_board_row():
    today = date(2026, 10, 22)
    rows = [{"symbol": "SPX", "spot": 7758.0, "flip": 7745.0, "asof": "2026-10-21",
             "regime": "long gamma (> flip, move-damping)", "spot_vs_flip_pct": 0.17}]
    _apply_pre_open_spot(rows, {"spot": 7690.2, "label": "08:35 index chain"}, today)
    r = rows[0]
    assert r["spot"] == 7690.2 and r["close_spot"] == 7758.0
    assert r["spot_vs_flip_pct"] < 0 and "short" in r["regime"]
    assert r["spot_src"] == "08:35 index chain"


def test_pre_open_spot_leaves_todays_board_alone():
    today = date(2026, 10, 22)
    rows = [{"symbol": "SPX", "spot": 7663.0, "flip": 7745.0, "asof": "2026-10-22", "regime": "x"}]
    _apply_pre_open_spot(rows, {"spot": 7690.2, "label": "chain"}, today)
    assert rows[0]["spot"] == 7663.0 and "spot_src" not in rows[0]
