"""Daily bars from FMP via the CVForge passthrough - the Yahoo exit, step 1 (2026-11-13 overnight).

Mithil 11/13: "we stop using yahoo". ``FmpPriceSource`` satisfies ``PriceDataSource`` with
FMP ``historical-price-eod/full`` (open/high/low/close/volume) on the existing CVForge key.
``FmpFirstPriceSource`` = FMP first; Yahoo ONLY when FMP has no rows for a symbol AND
``PRICES_YAHOO_FALLBACK`` is on (default on for the first week so nothing goes dark); every
fallback is logged ``prices.yahoo_fallback`` so the last Yahoo-only symbols are visible.
``scripts/yahoo_exit_check.py`` compares FMP vs the stored Yahoo bars before the fallback is
switched off. Rule 1: only ``clients/`` talks to vendors.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import structlog

from trading_intel.errors import DataSourceError

log = structlog.get_logger(__name__)
_FMP_SYMBOL_MAP = {"SPX": "^GSPC", "VIX": "^VIX", "NDX": "^NDX", "RUT": "^RUT", "DJI": "^DJI"}
_COLUMNS = ("date", "open", "high", "low", "close", "volume")
_PERIOD_DAYS = {"5d": 10, "1mo": 35, "3mo": 100, "6mo": 190, "1y": 370, "2y": 735, "5y": 1830, "10y": 3660}


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


class FmpFirstPriceSource:
    """FMP first; Yahoo only for symbols FMP can't serve, while the fallback switch is on."""

    def __init__(self, fmp: FmpPriceSource, yahoo: object | None, *, allow_yahoo: bool = True) -> None:
        self._fmp, self._yahoo, self._allow = fmp, yahoo, allow_yahoo

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        try:
            df = self._fmp.daily_history(symbol, period=period)
        except DataSourceError as exc:
            log.warning("prices.fmp_failed", symbol=symbol, error=str(exc))
            df = pd.DataFrame(columns=_COLUMNS)
        if not df.empty or not self._allow or self._yahoo is None:
            return df
        log.warning("prices.yahoo_fallback", symbol=symbol)
        return self._yahoo.daily_history(symbol, period=period)  # type: ignore[attr-defined]
