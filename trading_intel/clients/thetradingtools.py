"""thetrading.tools new-highs / new-lows file (ADR-009) - the only module that talks to that site.

One free JSON file, no key: ``https://www.thetrading.tools/data/market_breadth/nh_nl.json``
(~2.8 MB, whole history from 2010-12-31, rows split NYSE / Nasdaq / S&P 500 / Nasdaq-100).
Their licence: free for personal / research use with attribution, no commercial
redistribution -> the rows live only in the NAS database, never in the (public) repo.
One GET a day. Parsing is pure and lives in ``trading_intel.equity.nhnl``.
"""

from __future__ import annotations

from typing import Any

import httpx

from trading_intel.errors import DataSourceError

NHNL_URL = "https://www.thetrading.tools/data/market_breadth/nh_nl.json"
_UA = "trading-intel/1.0 (personal research; daily single fetch)"


def fetch_nhnl(*, timeout: float = 60.0, url: str = NHNL_URL) -> list[dict[str, Any]]:
    """The ``data`` rows of nh_nl.json (oldest first as published)."""
    try:
        resp = httpx.get(url, timeout=timeout, headers={"User-Agent": _UA}, follow_redirects=True)
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DataSourceError(f"thetrading.tools nh_nl fetch failed: {exc}") from exc
    rows = body.get("data") if isinstance(body, dict) else body
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
