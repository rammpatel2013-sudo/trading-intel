"""Probe: does CVForge's FMP passthrough serve earnings-call transcripts on our
existing key (i.e. free — no new vendor)? Tries the likely FMP-stable endpoint
spellings and reports which return data + the payload shape.

Run (Windows, repo venv):
    .venv\\Scripts\\python scripts\\probe_transcripts.py
"""

from __future__ import annotations

from typing import Any

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import get_settings

# (endpoint, params) candidates for FMP-stable earnings-transcript APIs.
_CANDIDATES: list[tuple[str, dict[str, Any]]] = [
    ("earning-call-transcript-latest", {"limit": 1}),
    ("earning-call-transcript", {"symbol": "AAPL", "year": 2025, "quarter": 1}),
    ("earning-call-transcript", {"symbol": "AAPL"}),
    ("earning_call_transcript", {"symbol": "AAPL", "year": 2025, "quarter": 1}),
    ("earning-call-transcript-dates", {"symbol": "AAPL"}),
    ("earnings-transcript-list", {}),
    ("batch-earning-call-transcript", {"symbol": "AAPL", "year": 2025}),
]


def _summarize(data: Any) -> str:  # noqa: ANN401 (diagnostic — arbitrary JSON)
    if isinstance(data, list):
        if not data:
            return "empty list"
        first = data[0]
        if isinstance(first, dict):
            body = str(first.get("content") or first.get("transcript") or "")
            return f"list[{len(data)}] keys={sorted(first.keys())} content~{body[:140]!r}"
        return f"list[{len(data)}] first={str(first)[:120]!r}"
    if isinstance(data, dict):
        return f"dict keys={sorted(data.keys())}"
    return f"{type(data).__name__}: {str(data)[:140]!r}"


def main() -> None:
    client = CVForgeClient(get_settings())
    try:
        for endpoint, params in _CANDIDATES:
            try:
                data = client.fmp(endpoint, params)
                print(f"OK    {endpoint} {params} -> {_summarize(data)}")
            except Exception as exc:  # noqa: BLE001 (show whatever the vendor returns)
                print(f"FAIL  {endpoint} {params} -> {str(exc)[:160]}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
