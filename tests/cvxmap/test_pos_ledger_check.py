"""pos_ledger OI check (2026-11-13): a net position can't exceed the contract's open interest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import pos_ledger as pl


def test_cap_to_oi():
    assert pl.cap_to_oi(500, 300) == 300
    assert pl.cap_to_oi(-500, 300) == -300
    assert pl.cap_to_oi(200, 300) == 200
    assert pl.cap_to_oi(100, None) == 100  # OI unknown: unchanged
    assert pl.cap_to_oi(0, 5) == 0


def test_ledger_health_numbers():
    h = pl.ledger_health([(500, 300), (-100, 200), (50, 0)])
    assert h["n"] == 3 and h["impossible_n"] == 2
    assert h["impossible_pct"] == 66.7
    assert h["impossible_size_pct"] == 84.6  # (500 + 50) / 650
    assert h["excess_pct"] == 38.5  # (200 + 50) / 650
    assert h["ledger_vs_oi_pct"] == 80.0  # (300 + 100 + 0) / 500


def test_ledger_health_empty():
    assert pl.ledger_health([])["impossible_pct"] is None
