"""Phase 5 Vol EOD tab panels (pure)."""

from datetime import date, timedelta

from trading_intel.vol import vol_tab as vt


def test_term_vrp_panel_recovers_rv_and_spreads():
    rows = [(date(2026, 1, 1) + timedelta(days=i), 15.0 + i * 0.1, 17.0, 3.0) for i in range(30)]
    out = vt.term_vrp_panel(rows)
    now = out["now"]
    assert abs(now["rv21"] - (now["vix"] - 3.0)) < 1e-9
    assert abs(now["prem"] - 3.0) < 1e-9
    assert out["term_pct"] is not None and "VIX3M" in out["read"]


def test_straddle_panel_avg_and_fed():
    d0 = date(2026, 10, 1)
    rows = [(d0 + timedelta(days=i), 1.0 if i < 19 else 2.0) for i in range(20)]
    out = vt.straddle_panel(rows, {d0 + timedelta(days=19)})
    assert out["now"]["fed"] is True
    assert abs(out["now"]["avg20"] - 1.05) < 1e-9
    assert out["ratio"] > 1.8


def test_color_time_only_signs():
    as_of = date(2026, 11, 4)
    chain = [
        (as_of + timedelta(days=1), 100.0, "C", 1_000_000, 0.2),  # rolls off tomorrow
        (as_of + timedelta(days=30), 100.0, "C", 1_000_000, 0.2),  # ATM: gamma grows as T shrinks
    ]
    out = vt.color_from_chain(chain, 100.0, as_of)
    assert out["roll_off"] < 0
    assert out["color_live"] > 0
    assert abs(out["gamma_next"] - (out["gamma_now"] + out["color_total"])) < 1e-3


def test_gex_change_series():
    s = vt.gex_change_series([(date(2026, 1, i), float(i)) for i in range(1, 5)])
    assert [x["chg"] for x in s] == [1.0, 1.0, 1.0]
