"""Earnings-call transcript access via the CVForge FMP passthrough.

Free on the existing CVForge key (probe 2026-07-16) — no new vendor. All HTTP
funnels through ``CVForgeClient.fmp`` (rule 1: CVForge is spoken only in
``clients/cvforge.py``), so this is a thin normalizing wrapper over three FMP
endpoints:

    earning-call-transcript-dates  -> list of {date, fiscalYear, quarter} per symbol
    earning-call-transcript        -> {content, date, period, symbol, year} for one call
    earning-call-transcript-latest -> newest transcripts market-wide (metadata)
"""

from __future__ import annotations

from typing import Any

from trading_intel.clients.cvforge import CVForgeClient


def available_quarters(client: CVForgeClient, symbol: str) -> list[dict[str, Any]]:
    """Available transcript quarters for ``symbol``, newest-first."""
    data = client.fmp("earning-call-transcript-dates", {"symbol": symbol.upper()})
    rows = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    return sorted(
        rows, key=lambda r: (r.get("fiscalYear") or 0, r.get("quarter") or 0), reverse=True
    )


def fetch(client: CVForgeClient, symbol: str, year: int, quarter: int) -> dict[str, Any] | None:
    """Full transcript for one (symbol, year, quarter), or ``None`` if unavailable."""
    data = client.fmp(
        "earning-call-transcript",
        {"symbol": symbol.upper(), "year": year, "quarter": quarter},
    )
    rec = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    return rec if isinstance(rec, dict) and rec.get("content") else None


def latest_two(client: CVForgeClient, symbol: str) -> list[dict[str, Any]]:
    """The two most recent transcripts (this quarter + prior), newest-first.

    Fetches down the available-quarters list (up to 4 deep to skip any gaps) until
    two full transcripts are collected — exactly what the QoQ inflection read needs.
    """
    out: list[dict[str, Any]] = []
    for q in available_quarters(client, symbol)[:4]:
        rec = fetch(client, symbol, q.get("fiscalYear"), q.get("quarter"))
        if rec:
            out.append(rec)
        if len(out) == 2:
            break
    return out
