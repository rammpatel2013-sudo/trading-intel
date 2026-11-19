"""scripts/oi_am.py mid_blocks (2026-11-19): a big mid/inside print group with no parsed strike crashed the
08:35 job on 11/18 and 11/19 (TypeError in k_strike). It is now skipped and counted; the rest still read."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import trading_intel.memory.db as db

_P = Path(__file__).resolve().parents[1] / "scripts" / "oi_am.py"
_spec = importlib.util.spec_from_file_location("oi_am_script", _P)
oi = importlib.util.module_from_spec(_spec)
sys.modules["oi_am_script"] = oi
_spec.loader.exec_module(oi)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, q, params):
        return _Result(self._rows)


def _factory(rows):
    return lambda settings: (lambda: _Session(rows))


def _blk(root, strike, cp="C", prem=250_000.0):
    return {"root": root, "expiry": date(2026, 12, 18), "strike": strike, "cp": cp,
            "prem": prem, "size": 100, "n": 3, "loc": "mid"}


def test_no_strike_rows_are_skipped(monkeypatch, capsys):
    rows = [_blk("NVDA", None), _blk("NVDA", 200.0), _blk("AAPL", None, "P")]
    monkeypatch.setattr(db, "make_session_factory", _factory(rows))
    rows_all = [{"symbol": "NVDA", "expiry": "2026-12-18", "strike": 200.0, "cp": "C", "d_oi": 500.0,
                 "d_iv_rel": 0.5, "tag": "BTO"},
                {"symbol": "AAPL", "expiry": "2026-12-18", "strike": None, "cp": "P", "d_oi": 10.0,
                 "d_iv_rel": None, "tag": "NA"}]
    out = oi.mid_blocks(object(), "2026-11-18", rows_all)
    assert [b["contract"] for b in out] == ["NVDA 2026-12-18 200C"]
    assert out[0]["d_oi"] == 500.0 and out[0]["vendor_split"] == "BTO"
    assert "skipped 2 big mid/inside print group(s) with no strike" in capsys.readouterr().out


def test_all_rows_have_strikes_unchanged(monkeypatch, capsys):
    rows = [_blk("SPY", 600.0, "P"), _blk("QQQ", 500.5)]
    monkeypatch.setattr(db, "make_session_factory", _factory(rows))
    out = oi.mid_blocks(object(), "2026-11-16", [])
    assert [b["contract"] for b in out] == ["SPY 2026-12-18 600P", "QQQ 2026-12-18 500.5C"]
    assert all(b["read"] == "no OI change" for b in out)
    assert "skipped" not in capsys.readouterr().out
