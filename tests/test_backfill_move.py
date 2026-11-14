"""backfill_move.parse_rows - FMP ^MOVE rows -> {date: close} (2026-11-14)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "backfill_move", Path(__file__).resolve().parents[1] / "scripts" / "backfill_move.py"
)
bm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bm)  # type: ignore[union-attr]


def test_parse_rows_price_then_close_and_junk() -> None:
    rows = [
        {"symbol": "^MOVE", "date": "2026-11-12", "price": 74.3},
        {"symbol": "^MOVE", "date": "2026-11-13", "close": 76.1},
        {"symbol": "^MOVE", "date": "bad", "price": 1.0},
        {"symbol": "^MOVE", "date": "2026-11-11", "price": None},
        {"symbol": "^MOVE", "date": "2026-11-10", "price": 0},
        "junk",
    ]
    assert bm.parse_rows(rows) == {date(2026, 11, 12): 74.3, date(2026, 11, 13): 76.1}
    assert bm.parse_rows({"error": "x"}) == {}
