"""Daily price-history clients (``PriceDataSource`` implementations).

yfinance is the default free source for daily OHLCV bars. All vendor calls live
here so downstream code (the backfill/quotes job, the dashboard) depends only on
the ``PriceDataSource`` Protocol, mirroring how options data flows through
``OptionsDataSource`` (CLAUDE.md rule 1). A Schwab implementation can be added
later for more granular history without touching consumers.
"""

from __future__ import annotations

import pandas as pd
import structlog

from trading_intel.errors import DataSourceError

log = structlog.get_logger(__name__)

# Watchlist symbols that need a different ticker on the price vendor. SPX is the
# S&P 500 *index* on Convex; yfinance exposes the index level as ``^GSPC``.
_YF_SYMBOL_MAP = {
    "SPX": "^GSPC",
    "VIX": "^VIX",
    "NDX": "^NDX",
}

_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class YFinancePriceSource:
    """``PriceDataSource`` backed by yfinance daily bars."""

    def __init__(self, *, symbol_map: dict[str, str] | None = None) -> None:
        self._symbol_map = symbol_map or dict(_YF_SYMBOL_MAP)

    def vendor_symbol(self, symbol: str) -> str:
        """Map a watchlist symbol to its yfinance ticker (identity by default)."""
        return self._symbol_map.get(symbol.upper(), symbol)

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        """Daily OHLCV for ``symbol`` over ``period``, oldest first.

        Raises ``DataSourceError`` if yfinance is unavailable or the fetch
        fails. Returns an empty frame (with the expected columns) when the
        vendor simply has no rows for the symbol.
        """
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - dependency present in prod
            raise DataSourceError("yfinance is not installed") from exc

        yf_symbol = self.vendor_symbol(symbol)
        try:
            raw = yf.Ticker(yf_symbol).history(
                period=period, interval="1d", auto_adjust=False
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            raise DataSourceError(f"yfinance history failed for {symbol!r}: {exc}") from exc

        if raw is None or raw.empty:
            return pd.DataFrame(columns=_COLUMNS)

        df = raw.reset_index().rename(
            columns={
                "Date": "date",
                "Datetime": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        if "date" not in df.columns:
            raise DataSourceError(f"yfinance returned no date column for {symbol!r}")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        # Drop timezone, normalize to midnight so it maps cleanly to a DB ``date``.
        if hasattr(df["date"].dtype, "tz") and df["date"].dt.tz is not None:
            df["date"] = df["date"].dt.tz_localize(None)
        df["date"] = df["date"].dt.normalize()
        missing = [c for c in _COLUMNS if c not in df.columns]
        if missing:
            raise DataSourceError(f"yfinance frame for {symbol!r} missing {missing}")
        out = df[list(_COLUMNS)].dropna(subset=["date", "close"])
        return out.sort_values("date").reset_index(drop=True)


def fetch_yf_index_close(symbol: str, *, period: str = "5d") -> float | None:
    """Latest close for a Yahoo index symbol (e.g. ``^SDEX``, ``^SKEW``).

    Degrades to ``None`` on any fetch / shape failure so an upstream Yahoo
    outage never blocks the calling job. Mirrors ``CboeClient`` semantics.
    """
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover - dependency present in prod
        raise DataSourceError("yfinance is not installed") from exc

    try:
        raw = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception as exc:  # network / shape / parse - degrade to None
        log.warning("yf.fetch_failed", sym=symbol, error=str(exc))
        return None

    if raw is None or raw.empty or "Close" not in raw.columns:
        log.warning("yf.empty", sym=symbol)
        return None

    closes = raw["Close"].dropna()
    if closes.empty:
        return None
    try:
        return float(closes.iloc[-1])
    except (TypeError, ValueError):
        return None


def fetch_sdex(*, period: str = "5d") -> float | None:
    """Nations SkewDex Large-Cap close, via Yahoo (``^SDEX``).

    SDEX is owned by Nations and isn't on the Cboe CDN, so Yahoo is the source
    of truth here. Returns ``None`` on any fetch failure.
    """
    return fetch_yf_index_close("^SDEX", period=period)


def fetch_voli(*, period: str = "5d") -> float | None:
    """Nations VolDex Large-Cap close, via Yahoo (``^VOLI``).

    VOLI is Nations' ATM-only implied vol gauge for SPY/SPX — a cleaner
    "true IV" read than VIX (which is a strip and is sensitive to wing prices).
    """
    return fetch_yf_index_close("^VOLI", period=period)


def fetch_tdex(*, period: str = "5d") -> float | None:
    """Nations TailDex Large-Cap close, via Yahoo (``^TDEX``).

    TDEX normalizes the price of deep-OTM puts — pure tail-hedge demand.
    """
    return fetch_yf_index_close("^TDEX", period=period)
