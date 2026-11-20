"""Vol monitor (scripts/risk/vol_monitor.py, rule set vm-v1) - 2026-11-19."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "risk"))
import vol_monitor as vm  # noqa: E402


def _frame(n=400, seed=1, spike=False):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    walk = lambda base, sd: base + np.cumsum(rng.normal(0, sd, n)) * 0.1  # noqa: E731
    X = pd.DataFrame(index=idx)
    X["^SPX"] = 5000 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    X["^VIX"] = np.clip(walk(16, 1), 10, 60)
    X["^VIX9D"] = X["^VIX"] - 1
    X["^VIX3M"] = X["^VIX"] + 2
    X["^VIX6M"] = X["^VIX"] + 3
    X["^VVIX"] = np.clip(walk(90, 3), 60, 200)
    X["^COR1M"] = np.clip(walk(20, 2), 2, 90)
    X["^COR3M"] = X["^COR1M"] + 1
    X["^VIXEQ"] = X["^VIX"] + 18
    X["^VXN"] = X["^VIX"] + 4
    X["^MOVE"] = np.clip(walk(95, 2), 50, 200)
    X["^DSPX"] = np.clip(walk(35, 1), 10, 80)
    X["HYG"] = 80 + np.cumsum(rng.normal(0, 0.1, n))
    X["IEF"] = 95 + np.cumsum(rng.normal(0, 0.1, n))
    if spike:  # last 3 days: index fear jumps, curve inverts, correlation jumps, bonds calm and falling
        X.iloc[-3:, X.columns.get_loc("^VIX")] = [30, 33, 36]
        X.iloc[-3:, X.columns.get_loc("^VIX9D")] = [33, 37, 40]
        X.iloc[-3:, X.columns.get_loc("^VIX3M")] = [28, 30, 32]
        X.iloc[-3:, X.columns.get_loc("^COR1M")] = [60, 70, 80]
        X.iloc[-6:, X.columns.get_loc("^MOVE")] = [90, 89, 88, 87, 86, 85]
    return X


def test_panel_shape_and_json_safe():
    o = vm.build_panel(_frame())
    assert o["ruleset"] == "vm-v1"
    assert [g["name"] for g in o["gauges"]] == ["VIX", "VVIX", "COR1M", "COR3M", "VIXEQ", "VXN", "MOVE", "DSPX"]
    assert len(o["combos"]) == 9 and {c["id"] for c in o["combos"]} == set(vm.PRIORITY)
    json.dumps(o, allow_nan=False)          # risk_tab writes with allow_nan=False
    assert o["read"]["headline"] and o["read"]["sentences"]
    assert o["ai"]["ok"] is False           # no AI passed -> rule read only
    L = o["long"]                           # pair charts + full view: every series the same length as the dates
    n = len(L["dates"])
    assert n == min(len(_frame()), vm.LONG)
    assert all(len(v) == n for v in list(L["s"].values()) + list(L["p"].values()) + list(L["c"].values()))
    assert set(L["c"]) == set(vm.PRIORITY)


def test_spike_turns_on_panic_and_equity_hedge():
    o = vm.build_panel(_frame(spike=True))
    assert o["on"][:2] == ["panic_curve", "equity_hedge"]
    assert o["derived"]["term"]["shape"] == "inverted"
    assert "NOT confirming" in " ".join(o["read"]["sentences"])
    assert "broad" not in o["on"]


def test_percentile_has_no_lookahead():
    s = pd.Series(np.arange(300, dtype=float))
    p = vm.roll_pct(s)
    assert p.iloc[200] == 1.0               # rising series: today is always the top of its own past
    s2 = s.copy()
    s2.iloc[250:] = -1                      # changing the future must not change the past
    assert vm.roll_pct(s2).iloc[200] == p.iloc[200]


def test_missing_series_never_fire_high_rules():
    X = _frame()
    X["^MOVE"] = np.nan
    X["^VXN"] = np.nan
    F, P = vm.frame(X)
    C = vm.combos(F, P)
    assert not C["rates"].any() and not C["tech"].any() and not C["broad"].any()


def test_stats_forward_window():
    spx = pd.Series([100.0] * 10 + [96.0] + [100.0] * 30, index=pd.bdate_range("2024-01-01", periods=41))
    fw = vm.forward(spx)
    assert fw["w5"].iloc[5] == pytest.approx(-0.04)   # day 5 sees the 96 close on day 10
    assert np.isnan(fw["r20"].iloc[-1])


def test_episodes_count():
    m = pd.Series([0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1], dtype=bool)
    assert vm.episodes(m, gap=5) == 2


class _FakeLLM:
    def __init__(self, text):
        self.text = text

    def complete_json(self, prompt, model=None, max_tokens=400):
        return json.dumps({"read": self.text})


def test_ai_read_kept_when_numbers_check():
    o = vm.build_panel(_frame(spike=True))
    v = o["gauges"][0]["value"]
    good = vm.build_panel(_frame(spike=True), llm=_FakeLLM(f"Fear is very high with the VIX at {v:.2f}. Watch the curve."))
    assert good["ai"]["ok"] and str(round(v, 2)) in good["ai"]["text"]


def test_ai_read_dropped_on_invented_number():
    bad = vm.build_panel(_frame(spike=True), llm=_FakeLLM("The S&P will fall 12.7% next week."))
    assert bad["ai"]["ok"] is False and "dropped" in bad["ai"]["error"]


def test_ai_errors_never_raise():
    class Boom:
        def complete_json(self, *a, **k):
            raise ConnectionError("laptop asleep")
    o = vm.build_panel(_frame(), llm=Boom())
    assert o["ai"]["ok"] is False and "ConnectionError" in o["ai"]["error"]
