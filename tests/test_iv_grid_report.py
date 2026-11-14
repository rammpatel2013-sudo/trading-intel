"""tests for scripts/iv_grid_report.py (#60 IV grid, 2026-11-13 overnight)."""

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "scripts" / "iv_grid_report.py"
_spec = importlib.util.spec_from_file_location("iv_grid_report", _P)
ig = importlib.util.module_from_spec(_spec)
sys.modules["iv_grid_report"] = ig
_spec.loader.exec_module(ig)


def rows(sym, n, last30):
    out = []
    for i in range(n):
        d = date(2026, 1, 1) + timedelta(days=i)
        a30 = last30 if i == n - 1 else 0.15 + 0.0005 * (i % 40)
        out.append(
            {
                "symbol": sym,
                "ts": d,
                "tenor_dte": 30,
                "iv_atm": a30,
                "iv_put_25d": a30 + 0.03,
                "iv_call_25d": a30 - 0.01,
            }
        )
        out.append(
            {
                "symbol": sym,
                "ts": d,
                "tenor_dte": 90,
                "iv_atm": 0.18,
                "iv_put_25d": None,
                "iv_call_25d": None,
            }
        )
    return out


def test_grid_percentiles_and_tags():
    g = ig.grid(rows("SPY", 100, 0.30) + rows("QQQ", 100, 0.10) + rows("IWM", 10, 0.2))
    by = {r["symbol"]: r["cells"] for r in g}
    assert by["SPY"]["atm30"]["pct"] == 100.0 and by["SPY"]["atm30"]["tag"] == "rich"
    assert by["QQQ"]["atm30"]["pct"] == 1.0 and by["QQQ"]["atm30"]["tag"] == "cheap"
    assert by["SPY"]["term"]["v"] == round(0.30 / 0.18, 3)
    assert by["IWM"]["atm30"]["pct"] is None and by["IWM"]["atm30"]["n"] == 10
    t = ig.text(g)
    assert "IWM" in t and "n=10" in t and "▲" in t and "▼" in t


def test_pct_rank():
    assert ig.pct_rank([1, 2, 3, 4], 3) == 75.0
    assert ig.tag(None) == "–" and ig.tag(50) == "mid"
