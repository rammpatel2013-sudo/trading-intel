"""am_feed build_own (2026-11-13): STR = the straddle, 1σ = 1.25 x STR (was spot ± straddle labelled 1σ). Made-up numbers."""
import importlib.util
from pathlib import Path

_P = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap" / "am_feed.py"
_S = importlib.util.spec_from_file_location("am_feed_own_under_test", _P)
am = importlib.util.module_from_spec(_S)
_S.loader.exec_module(am)

TODAY = "2026-11-16"


def _ti(straddle=True, em=True):
    ti = {"walls": {"as_of": TODAY, "spot": 5000.0, "call_wall": 5050.0, "put_wall": 4950.0,
                    "call_top_strikes": [{"strike": 5025.0}], "put_top_strikes": [{"strike": 4975.0}]},
          "walls_0dte": {}, "profile": {"flip": 4990.0},
          "market_read": {"mechanics": {"spot": 5000.0, "gex_flip": 4990.0}}}
    if straddle:
        ti["straddle"] = {"lower": 4960.0, "upper": 5040.0}
    if em:
        ti["market_read"]["mechanics"]["expected_move"] = {"lower": 4962.0, "upper": 5042.0}
    return ti


def test_rails_str_and_true_sigma():
    o = am.build_own(_ti(), {"dma20": None, "dma50": None}, TODAY)
    assert o["rails"] == [{"name": "STR", "lo": 4960.0, "hi": 5040.0}, {"name": "1σ", "lo": 4950.0, "hi": 5050.0}]
    assert o["em"] == [4960.0, 5040.0]


def test_sigma_levels_are_rails_not_targets():
    o = am.build_own(_ti(), {"dma20": None, "dma50": None}, TODAY)
    rail = [x for x in o["levels"]["SPX"] if x["role"] == "rail"]
    assert {x["v"] for x in rail} <= {4950.0, 5050.0}           # may merge into the walls at the same price
    targets = {t for s in o["scenarios"] for tt in s["targets"] for t in tt} | {s["level"] for s in o["scenarios"]}
    assert not any("1σ" in x["label"] and x["role"] == "rail" and x["v"] in targets for x in o["levels"]["SPX"])
    assert any("1σ = 1.25 × STR" in c for c in o["context"])


def test_falls_back_to_expected_move():
    o = am.build_own(_ti(straddle=False), {"dma20": None, "dma50": None}, TODAY)
    assert o["rails"][0] == {"name": "STR", "lo": 4962.0, "hi": 5042.0}
    assert o["rails"][1] == {"name": "1σ", "lo": 4952.0, "hi": 5052.0}


def test_no_straddle_no_rails():
    o = am.build_own(_ti(straddle=False, em=False), {"dma20": None, "dma50": None}, TODAY)
    assert o["rails"] == [] and o["em"] is None
