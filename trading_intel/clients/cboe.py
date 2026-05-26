"""CBOE client — VVIX and the VIX term structure.

The only module that scrapes CBOE (CLAUDE.md rule 1). Reads the public delayed-
quote JSON feed CBOE serves from its CDN. **Endpoints verified live 2026-05-26**:
the response shape is ``{"timestamp", "data": {"current_price", ...}, "symbol"}``
and ``_parse_price`` (unwrap ``data`` → ``current_price``) reads it correctly.
Note: the less-liquid tenors (``_VIX9D`` / ``_VIX3M`` / ``_VIX6M``) return
``open/high/low = 0.0`` with ``current_price == close`` — the level is still
correct; only ``_VIX`` / ``_VVIX`` carry full intraday OHLC.

Term-structure tenors (CBOE's current index names):
- ``_VIX9D``  — 9-day  (formerly VXST)
- ``_VIX``    — 30-day VIX
- ``_VIX3M``  — 3-month (formerly VXV)
- ``_VIX6M``  — 6-month (formerly VXMT)

All reads degrade gracefully to ``None`` so a CBOE outage / shape change never
brings down the snapshot job. Descriptive macro inputs only (FlashAlpha rule 4).
"""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{sym}.json"

VVIX_SYM = "_VVIX"
TERM_SYMS = {"VIX9D": "_VIX9D", "VIX": "_VIX", "VIX3M": "_VIX3M", "VIX6M": "_VIX6M"}


class CboeClient:
    """Fetch CBOE delayed quotes. Inject an httpx-like ``client`` in tests."""

    def __init__(self, *, client: object | None = None, timeout: float = 10.0) -> None:
        self._client = client
        self._timeout = timeout

    def _get_json(self, sym: str) -> dict | None:
        try:
            if self._client is not None:
                resp = self._client.get(_BASE.format(sym=sym))
            else:
                import httpx

                resp = httpx.get(_BASE.format(sym=sym), timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # network / shape / parse — degrade to None
            log.warning("cboe.fetch_failed", sym=sym, error=str(exc))
            return None

    @staticmethod
    def _parse_price(payload: dict | None) -> float | None:
        if not payload:
            return None
        data = payload.get("data", payload)
        for key in ("current_price", "last", "close", "value", "price"):
            val = data.get(key) if isinstance(data, dict) else None
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return None

    def quote(self, sym: str) -> float | None:
        return self._parse_price(self._get_json(sym))

    def vvix(self) -> float | None:
        return self.quote(VVIX_SYM)

    def term_structure(self) -> dict[str, float | None]:
        """Map tenor label -> level for the VIX term-structure curve."""
        return {label: self.quote(sym) for label, sym in TERM_SYMS.items()}
