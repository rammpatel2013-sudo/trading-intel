"""tests for scripts/fly_window.py (#60 fly calculator, 2026-11-13 overnight)."""

import importlib.util
import sys
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "scripts" / "fly_window.py"
_spec = importlib.util.spec_from_file_location("fly_window", _P)
fw = importlib.util.module_from_spec(_spec)
sys.modules["fly_window"] = fw
_spec.loader.exec_module(fw)


def test_expiry_is_the_tent():
    assert float(fw.fly_value(6750, 6750, 25, 0.0, 0.15)) == 25.0
    assert float(fw.fly_value(6770, 6750, 25, 0.0, 0.15)) == 5.0
    assert float(fw.fly_value(6800, 6750, 25, 0.0, 0.15)) == 0.0


def test_value_bounded_and_band_shrinks_late():
    w = fw.window(6740, 6750, 25, 0.15, 5, 0.5)
    assert 0 < w["cost"] < 25 and w["level"] == round(w["cost"] * 1.5, 2)
    exp = w["days"][-1]
    lo, hi = exp["band"]
    assert abs((6750 - lo) - (25 - w["level"])) < 0.5 and abs((hi - 6750) - (25 - w["level"])) < 0.5
    assert [d["days_left"] for d in w["days"]] == [5, 4, 3, 2, 1, 0]


def test_compare_and_main(capsys):
    c = fw.compare_dte(6740, 6750, 25, 0.15, dtes=(1, 7))
    assert c[0]["cost"] > c[1]["cost"] * 0.5 and c[1]["max_over_cost"] > c[0]["max_over_cost"] * 0.5
    assert fw.main(["--spot", "6740", "--center", "6750", "--width", "25", "--dte", "3"]) == 0
    assert "day(s) left" in capsys.readouterr().out
