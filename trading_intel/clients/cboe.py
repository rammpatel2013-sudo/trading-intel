"""CBOE client - VVIX, VIX term structure, skew indices, and VIX1D (1-day IV).

The only module that scrapes CBOE (CLAUDE.md rule 1). Reads the public delayed-
quote JSON feed CBOE serves from its CDN.

Term-structure tenors (CBOE's current index names):
- ``_VIX9D``  - 9-day (formerly VXST)
- ``_VIX``    - 30-day VIX
- ``_VIX3M``  - 3-month (formerly VXV)
- ``_VIX6M``  - 6-month (formerly VXMT)

Skew indices added per ADR-003:
- ``_SKEW``   - Cboe SKEW Index, BKM third-moment estimator over OTM SPX
- ``SDEX``    - Nations SkewDex Large-Cap, ATM vs 1-sigma-OTM-put SPY skew

1-day implied vol (the "SVIX / -1 day IV" the vol-divergence report reads):
- ``_VIX1D``  - Cboe 1-Day Volatility Index (0DTE SPX expected vol), live quote,
  plus the full daily history CSV (``VIX1D_History.csv``, 2022-05-13 onward).

All reads degrade gracefully to ``None`` / ``[]`` so a CBOE outage / shape change
never brings down the snapshot job.
"""

from __future__ import annotations

from datetime import date, datetime

import structlog

log = structlog.get_logger(__name__)

_BASE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{sym}.json"
_HISTORY_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"

VVIX_SYM = "_VVIX"
TERM_SYMS = {"VIX9D": "_VIX9D", "VIX": "_VIX", "VIX3M": "_VIX3M", "VIX6M": "_VIX6M"}
VIX1D_SYM = "_VIX1D"  # Cboe 1-Day Volatility Index (0DTE SPX expected vol)

# Skew index symbols. Per ADR-003 section 3.2, the Nations SDEX is the primary
# signal input; Cboe SKEW is a cross-check on third-moment regime changes.
SKEW_SYM = "_SKEW"
SDEX_SYM = "SDEX"


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
        except Exception as exc:  # network / shape / parse - degrade to None
            log.warning("cboe.fetch_failed", sym=sym, error=str(exc))
            return None

    def _get_text(self, url: str) -> str | None:
        try:
            if self._client is not None:
                resp = self._client.get(url)
            else:
                import httpx

                resp = httpx.get(url, timeout=self._timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # network / shape - degrade to None
            log.warning("cboe.fetch_text_failed", url=url, error=str(exc))
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

    def vix1d(self) -> float | None:
        """Cboe VIX1D (1-Day Volatility Index) live/last quote. None on failure."""
        return self.quote(VIX1D_SYM)

    def vix1d_history(self) -> list[tuple[date, float]]:
        """Full daily VIX1D close history as ``[(date, close), ...]`` ascending.

        Parses the public ``VIX1D_History.csv`` (``DATE,OPEN,HIGH,LOW,CLOSE``,
        m/d/Y dates, 2022-05-13 onward). Returns ``[]`` on any fetch/parse error
        so the report degrades rather than dies.
        """
        txt = self._get_text(_HISTORY_CSV.format(name="VIX1D"))
        if not txt:
            return []
        out: list[tuple[date, float]] = []
        for line in txt.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                d = datetime.strptime(parts[0].strip(), "%m/%d/%Y").date()
                c = float(parts[4])
            except (ValueError, IndexError):
                continue
            out.append((d, c))
        out.sort(key=lambda t: t[0])
        return out

    def skew_index(self) -> float | None:
        """Cboe SKEW Index (30d, model-free third moment of SPX) close.

        Degrades to ``None`` on any fetch / shape failure so an EOD outage in
        the Cboe feed never blocks the skew job.
        """
        return self.quote(SKEW_SYM)

    def sdex(self) -> float | None:
        """Nations SkewDex Large-Cap (``SDEX``) - ATM vs 1-sigma-OTM-put SPY skew.

        The Nations index is more directly comparable across names because both
        moneyness and maturity are standardized to ATM / 1-sigma-OTM and 30d
        respectively. If the CBOE CDN doesn't expose SDEX directly (the symbol
        is owned by Bank of America), this returns ``None`` and the EOD job
        falls back to our own ``SPY`` proxy.
        """
        return self.quote(SDEX_SYM)

    def term_structure(self) -> dict[str, float | None]:
        """Map tenor label -> level for the VIX term-structure curve."""
        return {label: self.quote(sym) for label, sym in TERM_SYMS.items()}
