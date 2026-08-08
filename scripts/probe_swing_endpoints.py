"""Probe: which swing-dossier data endpoints actually work on THIS key?

Verifies, for a sample ticker, every FMP endpoint the swing dossier needs — on
BOTH routes: the direct free-tier ``FmpClient`` (``settings.FMP_API``) and the
CVForge Research passthrough (``CVForgeClient.fmp`` on the ``cv_live`` key). For
each concept it tries a few candidate ``/stable`` spellings and reports the first
that returns rows, so you know exactly what to wire (and what to route through
``edgartools`` instead because FMP premium-gates it).

Read-only, descriptive (rule 4). No data is stored. Run locally where the .env
lives (the key never leaves your machine):

    python -m scripts.probe_swing_endpoints AAPL
    python -m scripts.probe_swing_endpoints NET --year 2026 --quarter 1
"""

from __future__ import annotations

import argparse
import sys

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.clients.fmp import FmpClient
from trading_intel.config import get_settings
from trading_intel.errors import DataSourceError

# concept -> list of candidate (endpoint, params) to try, first-with-rows wins.
# params get ``symbol`` injected; extra keys are passed through.
CANDIDATES: dict[str, list[tuple[str, dict]]] = {
    "analyst-estimates": [("analyst-estimates", {"period": "annual", "limit": 4})],
    "price-target": [
        ("price-target-consensus", {}),
        ("price-target-summary", {}),
        ("price-target-news", {"limit": 5}),
    ],
    "grades / revisions": [
        ("grades-historical", {"limit": 20}),
        ("grades-consensus", {}),
        ("grades", {"limit": 20}),
        ("upgrades-downgrades", {"limit": 20}),
        ("ratings-historical", {}),
    ],
    "insider (Form 4)": [
        ("insider-trading/search", {"limit": 20}),
        ("insider-trading", {"limit": 20}),
        ("insider-trading/latest", {"limit": 20}),
    ],
    "institutional (13F)": [
        ("institutional-ownership/symbol-ownership", {}),
        ("institutional-ownership/holders", {}),
        ("institutional-ownership/latest", {}),
        ("form-13f", {}),
    ],
    "income-statement": [("income-statement", {"period": "quarter", "limit": 8})],
    "key-metrics": [("key-metrics", {"period": "quarter", "limit": 8})],
    "ratios": [("ratios", {"period": "quarter", "limit": 8})],
    "shares-float": [("shares-float", {})],
}


def _rows(payload: object) -> int | None:
    if payload is None:
        return None
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        return 1 if payload else 0
    return None


def _verdict(n: int | None, err: str | None) -> str:
    if err:
        return f"ERR {err}"
    if n is None:
        return "none"
    if n == 0:
        return "EMPTY (0)"
    return f"OK ({n})"


def _try_cvforge(cv: CVForgeClient, endpoint: str, params: dict) -> tuple[int | None, str | None]:
    try:
        return _rows(cv.fmp(endpoint, params)), None
    except DataSourceError as exc:
        msg = str(exc)
        code = msg.split("->", 1)[1].strip().split(":", 1)[0] if "->" in msg else msg[:24]
        return None, code


def _try_direct(fmp: FmpClient, endpoint: str, params: dict) -> tuple[int | None, str | None]:
    # FmpClient._get already degrades to None on HTTP error; None == failure here.
    payload = fmp._get(endpoint, **params)  # noqa: SLF001 (probe only)
    return _rows(payload), None


def probe(symbol: str, *, year: int, quarter: int) -> None:
    settings = get_settings()
    cv = CVForgeClient(settings)
    fmp = FmpClient(settings)
    sym = symbol.upper()

    print(f"\n== swing-dossier endpoint probe: {sym} ==")
    print(f"{'concept':<22} {'endpoint':<40} {'cv_live':<16} {'direct-fmp':<16}")
    print("-" * 96)
    try:
        for concept, cands in CANDIDATES.items():
            for endpoint, extra in cands:
                params = {"symbol": sym, **extra}
                cn, cerr = _try_cvforge(cv, endpoint, params)
                dn, derr = _try_direct(fmp, endpoint, dict(params))
                print(f"{concept:<22} {endpoint:<40} {_verdict(cn, cerr):<16} {_verdict(dn, derr):<16}")
                if (cn or 0) > 0 or (dn or 0) > 0:
                    break  # first spelling that returns rows wins this concept

        # transcript (needs year/quarter; bare symbol 502s upstream — see memory)
        tp = {"symbol": sym, "year": year, "quarter": quarter}
        cn, cerr = _try_cvforge(cv, "earning-call-transcript", tp)
        print(f"{'transcript':<22} {'earning-call-transcript':<40} {_verdict(cn, cerr):<16} {'(cv only)':<16}")
    finally:
        cv.close()

    print(
        "\nRead: OK = wire it; EMPTY/none/402/403 on both routes = premium-gated "
        "→ route 13F+Form4 through edgartools (free/keyless), short interest through "
        "FINRA (clients/finra.py). Confirm the winning spelling before coding the collector."
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe swing-dossier data endpoints on this key.")
    ap.add_argument("symbol", nargs="?", default="AAPL")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--quarter", type=int, default=1)
    args = ap.parse_args()
    probe(args.symbol, year=args.year, quarter=args.quarter)
    return 0


if __name__ == "__main__":
    sys.exit(main())
