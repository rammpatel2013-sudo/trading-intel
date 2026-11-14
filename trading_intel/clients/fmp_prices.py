"""Daily bars from FMP via the CVForge passthrough - the only price source since the Yahoo exit.

Step 1 (2026-11-13): FMP first, Yahoo as a logged fallback. Step 2 (2026-11-14, Mithil
"why we still use yahoo we have cvforge"): the fallback and Yahoo are gone; every job that
needs daily OHLCV uses ``fmp_price_source()`` (``FmpPriceSource`` on the CVForge key).
The 11/14 check (scripts run on the NAS): 665/746 stored symbols matched FMP within 0.2%;
the rest were stale stored rows, thin OTC/ADR names and corporate actions - none a blocker.
Rule 1: only ``clients/`` talks to vendors.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import structlog

from trading_intel.errors import DataSourceError

log = structlog.get_logger(__name__)
_FMP_SYMBOL_MAP = {"SPX": "^GSPC", "VIX": "^VIX", "NDX": "^NDX", "RUT": "^RUT", "DJI": "^DJI"}
_COLUMNS = ("date", "open", "high", "low", "close", "volume")
_PERIOD_DAYS = {"5d": 10, "1mo": 35, "3mo": 100, "6mo": 190, "1y": 370, "2y": 735, "5y": 1830,
                "10y": 3660, "max": 36500}


class FmpPriceSource:
    """``PriceDataSource`` backed by FMP end-of-day bars (CVForge passthrough)."""

    def __init__(self, cvforge: object, *, symbol_map: dict[str, str] | None = None) -> None:
        self._cv = cvforge
        self._map = symbol_map or dict(_FMP_SYMBOL_MAP)

    def vendor_symbol(self, symbol: str) -> str:
        return self._map.get(symbol.upper(), symbol.upper())

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        start = date.today() - timedelta(days=_PERIOD_DAYS.get(period, 1830))
        try:
            data = self._cv.fmp("historical-price-eod/full",  # type: ignore[attr-defined]
                                {"symbol": self.vendor_symbol(symbol), "from": start.isoformat()})
        except Exception as exc:  # noqa: BLE001 - vendor boundary: re-raise as our error
            raise DataSourceError(f"FMP history failed for {symbol!r}: {exc}") from exc
        rows = data if isinstance(data, list) else (data or {}).get("historical") or []
        rows = [r for r in rows if isinstance(r, dict) and r.get("close") is not None]
        if not rows:
            return pd.DataFrame(columns=_COLUMNS)
        df = pd.DataFrame(rows)
        for c in _COLUMNS:
            if c not in df.columns:
                df[c] = 0 if c == "volume" else None
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        out = df[list(_COLUMNS)].dropna(subset=["date", "close"])
        return out.sort_values("date").reset_index(drop=True)


def fmp_price_source(settings: object | None = None, *, timeout: float = 30.0) -> FmpPriceSource:
    """The default ``PriceDataSource``: FMP bars on the CVForge key (composition helper)."""
    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings

    return FmpPriceSource(CVForgeClient(settings or get_settings(), timeout=timeout))  # type: ignore[arg-type]
