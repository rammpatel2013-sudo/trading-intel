"""Save one of TailThatWagsDog's published desk briefs for the paper track (2026-11-14).

    python -m trading_intel.desk.record --file ttwd_20261113.json

JSON keys: brief_date (session date, YYYY-MM-DD), top_pick, two_leg, div_a, div_b, screen (lists of
tickers), note. Same as the MCP tool ``record_ttwd_picks``. Ticker lists only - no text from his page.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    from trading_intel.config import get_settings
    from trading_intel.mcp.desk_tools import record_ttwd
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    a = ap.parse_args()
    d = json.loads(Path(a.file).read_text(encoding="utf-8"))
    with make_session_factory(get_settings())() as s:
        print(record_ttwd(s, d["brief_date"], d.get("top_pick", ""), d.get("two_leg", []),
                          d.get("div_a", []), d.get("div_b", []), d.get("screen", []), d.get("note", "")))


if __name__ == "__main__":
    main()
