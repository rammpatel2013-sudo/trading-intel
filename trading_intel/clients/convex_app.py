"""ConvexValue app/data client — the extra /api endpoints convexlib doesn't wrap.

``clients/convex.py`` (convexlib, pro login) covers core chain / underlying /
exposures. This thin authenticated client covers the *additional* documented
ConvexValue endpoints — earnings + economic calendars, the native ``vflowratio``
flow scanner, flowchart net-flow, per-name IV term structure, and the dealer
matrix — via the SAME ConvexValue pro login. Like ``convex.py``, all ConvexValue
HTTP is spoken here (rule 1); downstream code consumes the returned JSON.

Probed live 2026-07-15 (see MEMORY ``convexvalue-extra-endpoints``). Descriptive
data only — not signals (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from trading_intel.clients import EarningsDate
from trading_intel.clients.earnings_parse import parse_earnings_calendar
from trading_intel.config import Settings
from trading_intel.errors import DataSourceError

_EPOCH = date(1970, 1, 1)


def day_id(d: date) -> int:
    """ConvexValue ``day_id`` = days since the Unix epoch (2026-05-22 = 20595)."""
    return (d - _EPOCH).days


class ConvexAppClient:
    """Authenticated client for ConvexValue's extra /api/data + /api/get endpoints."""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str = "https://convexvalue.com",
        timeout: float = 30.0,
    ) -> None:
        self._email = settings.CONVEX_EMAIL
        self._password = settings.CONVEX_PASSWORD.get_secret_value()
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={"User-Agent": "trading-intel", "Content-Type": "application/json"},
        )
        self._logged_in = False

    def login(self) -> None:
        """POST /api/access/login; the session cookie persists on the client."""
        self._request(
            "POST", "/api/access/login", json={"email": self._email, "password": self._password}
        )
        self._logged_in = True

    def _request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> Any:  # noqa: ANN401
        if not self._logged_in and path != "/api/access/login":
            self.login()
        try:
            resp = self._client.request(method, path, json=json, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            raise DataSourceError(
                f"ConvexApp {method} {path} -> {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise DataSourceError(f"ConvexApp {method} {path} failed: {exc}") from exc

    # ── the extra endpoints (raw JSON; shapes documented in the memory) ──
    def earnings_calendar(self, *, days: int = 30) -> dict:
        """GET /api/data/earn_cal -> {data: [header, rows]}."""
        return self._request("GET", "/api/data/earn_cal", params={"days": days})

    def upcoming_earnings(self, *, days: int = 30) -> list[EarningsDate]:
        """Typed earnings calendar — satisfies ``clients.EarningsCalendarSource``.

        Thin wrapper: pulls ``earn_cal`` and shapes it via ``parse_earnings_calendar``
        (see that module's live-schema caveat). No new vendor — same pro login.
        """
        return parse_earnings_calendar(self.earnings_calendar(days=days))

    def economic_calendar(self, *, days: int = 7) -> dict:
        """GET /api/data/econ_cal -> {data: [header, rows]}."""
        return self._request("GET", "/api/data/econ_cal", params={"days": days})

    def flow_scan(self, *, min_value: float = 1_000_000, limit: int = 25) -> dict:
        """Native vflowratio flow scanner (POST /api/data/und) -> {data: [rows]}."""
        # min_value/limit are int-coerced below (not user strings) -> not injectable.
        query = (
            f"select symbol from und where value > {int(min_value)} "  # noqa: S608
            f"order by vflowratio desc nulls last limit {int(limit)}"
        )
        params = ["symbol", "value", "price", "change", "vflowratio"]
        return self._request("POST", "/api/data/und", json={"params": params, "query": query})

    def flowchart(self, symbol: str, *, d: date | None = None) -> dict:
        """POST /api/data/flowchart -> {data: [header, rows]}. ``day`` needs a real day_id."""
        cols = ["flownet", "vflownet", "value_call_bs", "value_put_bs"]
        body = {"symbol": symbol.upper(), "cols": cols, "day": day_id(d or date.today())}
        return self._request("POST", "/api/data/flowchart", json=body)

    def trm_chain(self, symbols: list[str], *, params: list[str] | None = None) -> dict:
        """Per-name IV term structure (POST /api/get/trmchain) -> {data: [{series: [...]}]}."""
        body = {
            "symbols": [s.upper() for s in symbols],
            "params": params or ["volatility", "oi", "volm"],
        }
        return self._request("POST", "/api/get/trmchain", json=body)

    def matrix(self, symbol: str) -> dict:
        """Dealer positioning grid (POST /api/data/matrix) -> {data: {cells: [...]}}."""
        return self._request("POST", "/api/data/matrix", json={"symbol": symbol.upper()})

    def close(self) -> None:
        self._client.close()
