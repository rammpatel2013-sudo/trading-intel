"""Pin the CVForge FMP 13F endpoint + field spellings (and confirm access isn't paywalled).

Tries candidate ``/stable`` endpoints for a fund's 13F portfolio holdings by CIK and
dumps the raw payload so ``filings_fetch.FMP_13F_ENDPOINT`` + the ``holdings_from_fmp``
field names can be locked. FMP institutional endpoints may be paywalled on the CVForge
tier (the sentiment collector is parked for exactly that reason) — a 402/403/empty here
says access needs granting. Mirrors ``check_sentiment.py``.

    python scripts/probe_fmp_13f.py [CIK]      # default: Nantahala 1472322
"""

from __future__ import annotations

import sys

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings

# (endpoint, params) candidates — {cik} is substituted. Ordered most->least likely.
_CANDIDATES: list[tuple[str, dict | None]] = [
    ("institutional-ownership/extract", {"cik": "{cik}"}),
    ("institutional-ownership/extract", {"cik": "{cik}", "year": "2026", "quarter": "1"}),
    ("institutional-ownership/portfolio-holdings", {"cik": "{cik}"}),
    ("institutional-ownership/holdings", {"cik": "{cik}"}),
    ("institutional-ownership/latest", {"cik": "{cik}"}),
    ("institutional-ownership/dates", {"cik": "{cik}"}),
    ("form-thirteen/{cik}", None),
]


def main() -> None:
    cik = sys.argv[1] if len(sys.argv) > 1 else "1472322"
    client = CVForgeClient(get_settings())
    try:
        for endpoint, params in _CANDIDATES:
            ep = endpoint.replace("{cik}", cik)
            pr = {k: v.replace("{cik}", cik) for k, v in params.items()} if params else {}
            print(f"\n=== FMP {ep}  params={pr} ===")
            try:
                res = client.fmp(ep, pr)
            except Exception as exc:  # noqa: BLE001 — diagnostic: report and keep probing
                print("ERR:", type(exc).__name__, str(exc)[:200])
                continue
            if isinstance(res, list):
                print(f"list[{len(res)}]", "first-row keys:", list(res[0].keys()) if res else "[]")
                if res:
                    print("sample:", {k: res[0][k] for k in list(res[0])[:8]})
            elif isinstance(res, dict):
                print("dict keys:", list(res.keys()))
            else:
                print(repr(res)[:300])
    finally:
        client.close()


if __name__ == "__main__":
    main()
