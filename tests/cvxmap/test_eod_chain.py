"""eod_chain (2026-11-20): end-of-day full SPX chain for the ledger - window, file name, width guard."""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import eod_chain  # noqa: E402


def _t(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm)


def test_window():
    assert eod_chain.in_window(_t(2026, 11, 20, 16, 25))          # Fri after the settlement
    assert eod_chain.in_window(_t(2026, 11, 20, 17, 55))
    assert not eod_chain.in_window(_t(2026, 11, 20, 16, 10))      # before 16:15
    assert not eod_chain.in_window(_t(2026, 11, 20, 18, 5))       # counters reset ~18:00
    assert not eod_chain.in_window(_t(2026, 11, 21, 16, 25))      # Saturday


def test_own_file_never_raw():
    p = eod_chain.out_path("/x/data", _t(2026, 11, 23, 16, 25))
    assert p.endswith("eod_raw_20261123.ndjson.gz")
    assert "/raw_" not in p.replace("eod_raw_", "")               # the Map reads raw_*; this is never that


def test_width_guard():
    row = ["k"] + [0] * 48
    good = [{"chain": [[20777, [[7000.0, row]]]]}]
    bad = [{"chain": [[20777, [[7000.0, row[:-1]]]]]}]
    assert eod_chain.width_ok(good, 48) is True
    assert eod_chain.width_ok(bad, 48) is False
    assert eod_chain.width_ok([], 48) is None


def test_two_chunks_cover_all_expiries_one_per_minute():
    assert eod_chain.CHUNKS[0][0] == 0 and eod_chain.CHUNKS[-1][-1] >= 79
    assert eod_chain.GAP_S >= 60
