"""OpEx NOW vs POST-SET: feature naming and the monthly gate."""
from datetime import date
import math, importlib.util, sys
from pathlib import Path

from trading_intel.synthesis.opex_split import features, is_monthly_opex


def test_monthly_gate():
    assert is_monthly_opex(date(2026, 10, 16)) and is_monthly_opex(date(2026, 11, 20))
    assert not is_monthly_opex(date(2026, 10, 22))


def test_features_named_relative_to_spot():
    grid = [7500 + 5 * i for i in range(61)]          # 7500..7800
    spot = 7650.0
    # negative below 7560 (trapdoor at ~7560), cushion peak at 7600, pocket dip at 7630,
    # pin at 7650, wall at 7720, accel trough at 7780
    def v(k):
        return (-30 if k < 7560 else 0) + 40 * math.exp(-((k - 7600) / 12) ** 2) - 25 * math.exp(-((k - 7630) / 6) ** 2) \
               + 30 * math.exp(-((k - 7650) / 5) ** 2) + 80 * math.exp(-((k - 7720) / 10) ** 2) - 50 * math.exp(-((k - 7780) / 8) ** 2)
    vals = [v(k) for k in grid]
    names = {f["name"]: f["level"] for f in features(grid, vals, spot)}
    assert names["pin"] == 7650 and names["wall"] == 7720 and names["accel"] == 7780
    assert names["cushion"] == 7600 and names["pocket"] == 7630 and 7555 <= names["trapdoor"] <= 7565


def test_card_renders_from_payload():
    spec = importlib.util.spec_from_file_location("cockpit_report", Path("scripts/cockpit_report.py"))
    mod = importlib.util.module_from_spec(spec); sys.modules["cockpit_report"] = spec.loader.exec_module(mod) or mod
    grid = [7500 + 10 * i for i in range(31)]
    o = {"grid": grid, "now": [math.sin(i / 4) for i in range(31)], "post": [math.cos(i / 4) for i in range(31)], "spot": 7650.0,
         "now_flip": 7640.0, "post_flip": 7655.0, "now_features": [], "post_features": [{"name": "wall", "level": 7700.0, "value": 1.0}],
         "am_contracts": 10, "pm_contracts": 20, "am_oi": 12345, "pm_oi": 67890, "read": "r"}
    html = mod._opex_card({"opex": o})
    assert "POST-SET" in html and "wall <b>7,700</b>" in html and "<svg" in html
    assert mod._opex_card({"opex": None}) == ""
