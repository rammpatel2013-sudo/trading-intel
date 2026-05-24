"""Financial Modeling Prep client - company profile, financials, news (free tier).

The only module that talks to FMP (CLAUDE.md rule 1). Uses the current **stable**
API (``/stable/...``, ``symbol`` as a query param) - the legacy ``/api/v3/``
routes now 403 for free accounts. Free tier ~250 calls/day, 5y annual statements.
Every endpoint is best-effort and degrades to None / [] on failure. Descriptive
research input only (rule 4). Key from ``settings.FMP_API``.
"""
from __future__ import annotations

import structlog

from trading_intel.config import Settings

log = structlog.get_logger(__name__)

_BASE = "https://financialmodelingprep.com/stable"


class FmpClient:
    """Fetch FMP company data (stable API). Inject an httpx-like ``client`` in tests."""

    def __init__(self, settings: Settings, *, client: object | None = None, timeout: float = 15.0):
        key = settings.FMP_API
        self._key = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key)
        self._client = client
        self._timeout = timeout

    def _get(self, path: str, **params):
        params["apikey"] = self._key
        url = f"{_BASE}/{path}"
        try:
            if self._client is not None:
                resp = self._client.get(url, params=params)
            else:
                import httpx

                resp = httpx.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # network / 403 / shape - degrade gracefully
            log.warning("fmp.fetch_failed", path=path, error=str(exc))
            return None

    def profile(self, ticker: str) -> dict | None:
        """Company profile (companyName, sector, industry, description, marketCap, ...)."""
        data = self._get("profile", symbol=ticker)
        return data[0] if isinstance(data, list) and data else None

    def income_statement(self, ticker: str, *, limit: int = 2) -> list[dict]:
        """Recent annual income statements (revenue, netIncome, margins, ...)."""
        data = self._get("income-statement", symbol=ticker, period="annual", limit=limit)
        return data if isinstance(data, list) else []

    def news(self, ticker: str, *, limit: int = 8) -> list[dict]:
        """Recent stock-news items (title, text, site, url, publishedDate)."""
        data = self._get("news/stock", symbols=ticker, limit=limit)
        return data if isinstance(data, list) else []
