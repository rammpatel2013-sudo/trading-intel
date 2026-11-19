"""Guard (2026-11-14 audit): never derive a spot time from an options book's ``ts`` with a clock offset.

oi_chain_eod / index_walls_am stamp each book at 00:00 of its day, so "snapshot <= book ts + N minutes"
lands on the PREVIOUS day (the 11/12 close-odds bug: every EOD book priced at yesterday's close). The one
place allowed to pick a book's spot is ``trading_intel/market/book_spot.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BAD = re.compile(r"GreeksSnapshot\.ts\s*[<>]=?\s*[A-Za-z_]*ts\s*[+-]\s*timedelta")


def test_no_book_ts_plus_offset_spot_lookups():
    hits = []
    for base in ("trading_intel", "scripts"):
        for f in (ROOT / base).rglob("*.py"):
            if f.name == "book_spot.py" or "archive" in f.parts:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if BAD.search(line):
                    hits.append(f"{f.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "spot from book ts + offset - use market.book_spot.book_spot():\n" + "\n".join(hits)
