"""Quick check for the sentiment collector.

Prints the latest ``sentiment_snapshots`` rows; if the FMP-sourced fields came back
empty (endpoint-spelling mismatch), also dumps the raw CVForge FMP payloads for ORCL
so the exact ``/stable`` names can be pinned in ``trading_intel/sentiment/fmp_map.py``.

Run from the repo root:
    .venv\\Scripts\\python check_sentiment.py
"""

from __future__ import annotations

from sqlalchemy import text

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory

settings = get_settings()

print("=== sentiment_snapshots (latest 12) ===")
print("symbol | inst_pct | pt_avg | rating_buy | buy_share | consensus")
with make_session_factory(settings)() as s:
    rows = s.execute(
        text(
            "select symbol, inst_pct, pt_avg, rating_buy, buy_share, rating_consensus "
            "from sentiment_snapshots order by ts desc, symbol limit 12"
        )
    ).all()

for r in rows:
    print(r)

populated = any((r[1] is not None or r[2] is not None or r[3] is not None) for r in rows)
print("\nfields populated:", populated)

if not populated:
    print("\n>>> fields are empty -- dumping raw FMP payloads so spellings can be pinned:")
    client = CVForgeClient(settings)
    try:
        for endpoint in (
            "price-target-consensus",
            "grades-consensus",
            "institutional-ownership/symbol-ownership",
            "quote",
        ):
            print(f"\n--- raw FMP: {endpoint} (ORCL) ---")
            print(client.fmp(endpoint, {"symbol": "ORCL"}))
    finally:
        client.close()
