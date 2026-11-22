"""sell_gate_report page + split (2026-11-22)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _mod():
    spec = importlib.util.spec_from_file_location("sgr", ROOT / "scripts" / "sell_gate_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_split_and_page(tmp_path) -> None:
    m = _mod()
    for d, pl in (("20261118", 500), ("20261120", -900)):
        (tmp_path / d).mkdir()
        (tmp_path / d / "seven_tests.json").write_bytes(json.dumps(
            {"oi": {"summary": [{"test": "2 Fly @ gamma zero", "n": 2, "risk": 800, "pl": pl}]}}).encode())
    sp = m.split_by_gate(tmp_path, {"2026-11-18": "sell", "2026-11-20": "stop"}, "oi")
    assert sp["sell"]["2 Fly @ gamma zero"] == [2, 800, 500]
    assert sp["stop"]["2 Fly @ gamma zero"][2] == -900
    nxt = {"day": "2026-11-23", "bucket": "stop", "count": 4,
           "signs": {"short_gamma": True, "front_vol_rising": True, "vix_above_25": True, "skew_high": None},
           "values": {"vix": 35.9, "skew_pctile": None, "front_vol_now": 0.40}}
    h = m.page(nxt, sp, "oi")
    assert "3–4 signs" in h and "−$900" in h and "<script" not in h
