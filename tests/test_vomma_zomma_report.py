"""scripts/vomma_zomma_report.py with a fake read-only Db (2026-11-13 overnight)."""

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "scripts" / "vomma_zomma_report.py"
_spec = importlib.util.spec_from_file_location("vomma_zomma_report", _P)
vr = importlib.util.module_from_spec(_spec)
sys.modules["vomma_zomma_report"] = vr
_spec.loader.exec_module(vr)


class FakeDb:
    def books(self):
        out = []
        for i in range(25):
            d = datetime(2026, 10, 1, 17, 15) + timedelta(days=i)
            out += [(d, "eod"), (d.replace(hour=8, minute=35), "am")]
        return out

    def spot(self, ts):
        return 6000.0

    def rows(self, ts, kind):
        e = ts.date() + timedelta(days=40)
        return {
            (e, 6000.0, "C", False): {"oi": 1000.0, "iv": 0.16},
            (ts.date() + timedelta(days=3), 5900.0, "P", False): {"oi": 800.0, "iv": 0.2},
        }


def test_update_backfills_once_and_text():
    h = vr.update(FakeDb(), [])
    assert len(h) == 25 and h[0]["date"] == "2026-10-01"
    h2 = vr.update(FakeDb(), h)
    assert len(h2) == 25
    f = vr.vz.flags(h2)
    t = vr.text(f, h2[-1])
    assert "31-90 DTE" in t and "Vomma / zomma" in t
