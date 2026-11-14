"""scripts/macro_rails.py end to end with fake FRED / FMP clients (2026-11-13 overnight)."""

import importlib.util
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

_P = Path(__file__).resolve().parents[1] / "scripts" / "macro_rails.py"
_spec = importlib.util.spec_from_file_location("macro_rails_script", _P)
ms = importlib.util.module_from_spec(_spec)
sys.modules["macro_rails_script"] = ms
_spec.loader.exec_module(ms)


class FakeFred:
    def series(self, sid):
        idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=40, freq="B")
        return pd.Series([3.0] * 39 + [3.2], index=idx)

    def release_dates(self, rid):
        return [date.today() - timedelta(days=30 * i) for i in range(1, 12)] if rid == 10 else []


class FakePx:
    def daily_history(self, sym, period="5y"):
        n = 600
        d = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="D")
        base = {"SPY": 600, "QQQ": 500, "HYG": 80, "IEI": 118, "^MOVE": 90, "SPX": 6000}[sym]
        return pd.DataFrame({"date": d, "close": [base * (1 + 0.001 * (i % 7)) for i in range(n)]})


def test_build_and_text(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "OUT", tmp_path)
    vm = tmp_path / "data"
    vm.mkdir()
    (vm / "vixmap_20261113.json").write_text(
        json.dumps(
            {
                "exps": [
                    {"e": "2026-11-18", "F": 18.0},
                    {"e": "2026-12-16", "F": 19.0},
                    {"e": "2027-01-20", "F": 19.5},
                ]
            }
        )
    )
    (tmp_path / "events.json").write_text(
        json.dumps(
            {
                "events": [
                    {
                        "d": (date.today() + timedelta(days=3)).isoformat(),
                        "t": "08:30",
                        "kind": "Data",
                        "label": "CPI",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(ms, "DATA", vm)
    r = ms.build(FakeFred(), FakePx())
    assert r["credit"]["spark"] is True and r["vx"]["found"] and r["events"][0]["kind"] == "CPI"
    assert (tmp_path / "vx_hist.csv").exists() and len(r["vx_hist"]) == 1
    t = ms.telegram_text(r)
    assert "SPARK" in t and "VX2−VX1 +1.00" in t and "CPI" in t
