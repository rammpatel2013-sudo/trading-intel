"""One-off diagnostic: which fundamentals columns populated + the live FMP field
names, so ``factors/fmp_map.py`` candidate keys can be corrected precisely.

Run (Windows, repo venv):
    .venv\\Scripts\\python scripts\\probe_fmp_fields.py
"""

from __future__ import annotations

from dataclasses import fields

from sqlalchemy import func, select

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings
from trading_intel.factors.compute import FactorInputs
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import FundamentalsSnapshot as F

_RAW = [f.name for f in fields(FactorInputs) if f.name != "symbol"]


def main() -> None:
    settings = get_settings()

    # 1) which of our columns actually populated (non-null count / total)
    with make_session_factory(settings)() as s:
        total = s.execute(select(func.count()).select_from(F)).scalar() or 0
        print(f"=== fundamentals_snapshots: {total} rows; non-null per column ===")
        for c in _RAW:
            n = s.execute(select(func.count(getattr(F, c))).select_from(F)).scalar() or 0
            flag = "  <-- ALL NULL" if n == 0 else ""
            print(f"  {c:16} {n}/{total}{flag}")

    # 2) the real FMP keys for one name, so we can map the null ones correctly
    client = CVForgeClient(settings)
    try:
        for ep in ("ratios-ttm", "key-metrics-ttm", "financial-growth", "profile"):
            try:
                data = client.fmp(ep, {"symbol": "AAPL"})
            except Exception as exc:  # noqa: BLE001 (diagnostic — show whatever failed)
                print(f"\n=== {ep}: ERROR {exc} ===")
                continue
            rec = data[0] if isinstance(data, list) and data else data
            keys = sorted(rec.keys()) if isinstance(rec, dict) else rec
            print(f"\n=== {ep} keys (AAPL) ===\n{keys}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
