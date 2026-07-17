"""Retry the CVForge-allowlisted sentiment endpoints (the 502s are transient upstream
hiccups, not access denials) and dump the exact field names so the mapping can be pinned.

Run from the repo root:
    .venv\\Scripts\\python probe_fmp_sentiment.py
"""

from __future__ import annotations

import json
import time

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.errors import DataSourceError

ENDPOINTS = [
    "institutional-ownership/symbol-positions-summary",
    "analyst-estimates",
    "quote",
]


def fetch(client: CVForgeClient, ep: str, tries: int = 5):
    last = "?"
    for _ in range(tries):
        try:
            return client.fmp(ep, {"symbol": "ORCL"})
        except DataSourceError as exc:
            last = str(exc).splitlines()[0][:90]
            time.sleep(1.5)
    return f"__FAILED__ after {tries} tries: {last}"


def main() -> None:
    client = CVForgeClient(get_settings())
    try:
        for ep in ENDPOINTS:
            print("\n" + "=" * 72)
            print(ep)
            result = fetch(client, ep)
            if isinstance(result, str):
                print(result)
                continue
            is_list = isinstance(result, list)
            record = result[0] if (is_list and result) else result
            print("type:", "list" if is_list else type(result).__name__, "len:", len(result) if is_list else "-")
            print(json.dumps(record, indent=2, default=str)[:1600])
    finally:
        client.close()


if __name__ == "__main__":
    main()
