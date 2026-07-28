"""Pin the CVForge/FMP endpoints for the research report's non-options panels.

Dumps raw payloads (keys + a sample) for a symbol so the field names can be locked into
the fundamentals / institutional / analyst / transcript fetchers — same pattern as
``scripts/probe_fmp_13f.py`` (which nailed the 13F endpoint). FMP institutional/analyst
endpoints may be tier-gated; a 402/403/empty says which need granting.

    python scripts/probe_fmp_fundamentals.py TAP
"""

from __future__ import annotations

import sys

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings

# (endpoint, params) candidates — {sym} substituted. Grouped by panel.
_CANDIDATES: list[tuple[str, dict | None]] = [
    # snapshot / profile
    ("profile", {"symbol": "{sym}"}),
    ("quote", {"symbol": "{sym}"}),
    # valuation / quality (TTM)
    ("key-metrics-ttm", {"symbol": "{sym}"}),
    ("ratios-ttm", {"symbol": "{sym}"}),
    ("key-metrics", {"symbol": "{sym}", "period": "annual", "limit": 1}),
    # statements (SBC, FCF, net debt, margins)
    ("income-statement", {"symbol": "{sym}", "period": "annual", "limit": 2}),
    ("cash-flow-statement", {"symbol": "{sym}", "period": "annual", "limit": 2}),
    ("balance-sheet-statement", {"symbol": "{sym}", "period": "annual", "limit": 1}),
    # estimates / analyst
    ("analyst-estimates", {"symbol": "{sym}", "period": "annual", "limit": 2}),
    ("price-target-consensus", {"symbol": "{sym}"}),
    ("grades-consensus", {"symbol": "{sym}"}),
    ("earnings-surprises", {"symbol": "{sym}"}),
    # institutional + insider
    ("institutional-ownership/symbol-ownership", {"symbol": "{sym}"}),
    ("insider-trading/search", {"symbol": "{sym}", "limit": 10}),
    # transcript availability
    ("earning-call-transcript-dates", {"symbol": "{sym}"}),
]


def main() -> None:
    sym = (sys.argv[1] if len(sys.argv) > 1 else "TAP").upper()
    client = CVForgeClient(get_settings())
    try:
        for endpoint, params in _CANDIDATES:
            pr = {k: v.replace("{sym}", sym) if isinstance(v, str) else v for k, v in (params or {}).items()}
            print(f"\n=== FMP {endpoint}  params={pr} ===")
            try:
                res = client.fmp(endpoint, pr)
            except Exception as exc:  # noqa: BLE001 — diagnostic: report and keep probing
                print("ERR:", type(exc).__name__, str(exc)[:180])
                continue
            if isinstance(res, list):
                print(f"list[{len(res)}]", "first-row keys:", list(res[0].keys()) if res else "[]")
                if res:
                    print("sample:", {k: res[0][k] for k in list(res[0])[:10]})
            elif isinstance(res, dict):
                print("dict keys:", list(res.keys()))
                print("sample:", {k: res[k] for k in list(res)[:10]})
            else:
                print(repr(res)[:300])
    finally:
        client.close()


if __name__ == "__main__":
    main()
