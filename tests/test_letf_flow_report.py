"""LETF flow panel (2026-11-13 overnight, #50)."""
import importlib.util
import pathlib
from datetime import date, timedelta

from trading_intel.etf_flows.descriptors import SharesRow
from trading_intel.etf_flows.registry import meta_for

spec = importlib.util.spec_from_file_location("lfr", pathlib.Path("scripts/letf_flow_report.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_summarize_and_render():
    bull = next(s for s in ("TQQQ", "SOXL", "SPXL", "UPRO") if meta_for(s) and meta_for(s).leverage > 0)
    bear = next(s for s in ("SQQQ", "SOXS", "SPXS", "SPXU") if meta_for(s) and meta_for(s).leverage < 0)
    d0 = date(2026, 11, 10)
    rows = {bull: [SharesRow(d0 + timedelta(days=i), 1_000_000 + 10_000 * i, 50.0 + i) for i in range(3)],
            bear: [SharesRow(d0 + timedelta(days=i), 2_000_000 - 5_000 * i, 20.0 - 0.2 * i) for i in range(3)]}
    sm = m.summarize(rows)
    assert len(sm["days"]) == 2 and sm["days"][-1][1] > 0 > sm["days"][-1][2]
    html = m.render(sm)
    assert "leaning BULL" in html and "<svg" in html
