"""Unit tests for the market synthesis brain (pure; dicts in → read out)."""
from __future__ import annotations

from trading_intel.synthesis.market_read import build_read


def _pos(amp, flip=6300, spot=6350, lo=6320, hi=6380):
    return {
        "spot": spot,
        "regime": {"amplifying": amp, "gex_flip": flip, "dist_to_flip": 0.01},
        "expected_move": {"lower": lo, "upper": hi},
        "dex": {"lean": "long delta"},
    }


def _breadth(above, div, pct200=65):
    return {
        "above_bbl": above,
        "bull_bear_line": 6000.0,
        "dist_to_bbl": 0.05,
        "pct_above_200": pct200,
        "divergence": {"state": div, "length": 3},
    }


def _vol(vix=14, vvix=95, term=-2.0):
    return {"vix": vix, "vvix": vvix, "term_9d_3m": term}


def test_aligned_constructive():
    r = build_read(_pos(amp=False), _breadth(True, "confirming"), _vol())
    assert r["confluence"]["score"] == "aligned-constructive"
    assert r["confluence"]["aligned"] is True
    assert "grind" in r["path"]
    assert r["regime"]["state"] == "bull-intact"
    assert r["mechanics"]["state"].startswith("long-gamma")
    assert r["weather"]["state"] == "calm-contango"


def test_coiled_spring_flag_pin_plus_high_vvix():
    r = build_read(_pos(amp=False), _breadth(True, "confirming"), _vol(vvix=125))
    assert any("coiled spring" in f for f in r["cross_pillar_flags"])
    assert r["weather"]["vvix_elevated"] is True


def test_breadth_div_under_pin_is_mixed_with_tension():
    r = build_read(_pos(amp=False), _breadth(True, "bearish_div", pct200=55), _vol())
    assert r["confluence"]["score"] == "mixed"
    assert r["confluence"]["tension"]
    assert any("top setup" in f for f in r["cross_pillar_flags"])


def test_narrow_breadth_plus_short_gamma_fragility():
    r = build_read(_pos(amp=True), _breadth(False, "bearish_div", pct200=30),
                   _vol(vix=26, vvix=130, term=1.5))
    assert r["confluence"]["score"] == "aligned-defensive"
    assert any("fragility multiplier" in f for f in r["cross_pillar_flags"])
    assert "air pocket" in r["path"]


def test_triggers_include_flip_and_bbl_and_newsletter():
    news = {"sources": {"DOC": {"scenarios": [
        {"trigger": "SPX loses 6300", "consequence": "air to 6250", "direction": "bearish"}]}}}
    r = build_read(_pos(amp=False), _breadth(True, "confirming"), _vol(), news)
    kinds = {t["source"] for t in r["triggers"]}
    assert "ours" in kinds and "DOC" in kinds
    assert any("gamma flip" in t["trigger"] for t in r["triggers"] if t["source"] == "ours")
    assert any("Bull/Bear Line" in t["trigger"] for t in r["triggers"] if t["source"] == "ours")


def test_levels_ladder_sorted_and_has_spot():
    r = build_read(_pos(amp=False, flip=6300, spot=6350, lo=6320, hi=6380),
                   _breadth(True, "confirming"), _vol())
    vals = [x["value"] for x in r["levels"]]
    assert vals == sorted(vals)
    assert any(x["name"] == "spot" for x in r["levels"])


def test_empty_inputs_degrade_cleanly():
    r = build_read({}, {}, {})
    assert r["mechanics"]["state"] == "gamma n/a"
    assert r["weather"]["state"] == "vol n/a"
    assert r["regime"]["state"] == "unknown"
    assert isinstance(r["narrative"], str) and r["narrative"]
