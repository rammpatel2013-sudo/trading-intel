"""External data clients.

The `OptionsDataSource` Protocol is the contract every options-data vendor
must satisfy. Code in `greeks/`, `strategies/`, `synthesis/`, and `dashboard/`
depends on this Protocol — NOT on `ConvexClient` directly. This keeps vendor
swap-out cheap.
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class OptionsDataSource(Protocol):
    """Contract for any options data vendor (Convex, Schwab, Barchart, Tradier)."""

    def chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        """Return a normalized options chain.

        Required columns:
            strike, expiration, opt_kind (C/P), delta, gamma, theta, vega,
            vanna, charm, iv, oi, volume, gxoi, dxoi, vxoi, cxoi
        """
        ...

    def underlying(
        self,
        symbols: list[str],
        *,
        time_buckets: tuple[str, ...] = ("5m", "15m", "30m"),
    ) -> pd.DataFrame:
        """Return per-symbol underlying-level data including flow metrics."""
        ...

    def spot(self, symbol: str) -> float:
        """Return the current underlying price for ``symbol``.

        A cheap price-only lookup (no chain pull) for callers that need spot to
        anchor an exposure computation, e.g. the intraday 0DTE flow collector.
        """
        ...


    def exposures(self, symbol: str, exps: tuple[int, ...] = (1, 2, 3)) -> dict:
        """Aggregate GEX/DEX/VEX/CHEX + flip point for one symbol."""
        ...

    def flow_chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        """Per-strike flow chain: premium ($ notional), volume, IV, OI.

        Normalized to ``opt_kind``/``premium``/``volume``/``iv`` so it feeds
        ``strategies.options_flow.aggregate_flow`` directly.
        """
        ...

    def time_and_sales(self, symbol: str, *, limit: int = 200) -> pd.DataFrame:
        """Per-trade time & sales (single sweeps / blocks) for ``symbol``."""
        ...

    def health(self) -> dict:
        """Vendor connectivity, rate-limit status, last call latency."""
        ...


class PriceDataSource(Protocol):
    """Contract for any daily price-history vendor (yfinance, Schwab, ...).

    Kept separate from ``OptionsDataSource``: price history is a different
    concern (and a different vendor) from the options chain. yfinance covers
    daily bars today; a Schwab implementation can satisfy the same Protocol
    later to add more granular history.
    """

    def daily_history(self, symbol: str, *, period: str = "5y") -> pd.DataFrame:
        """Return a daily OHLCV frame for ``symbol``, oldest first.

        Required columns: ``date`` (naive datetime, normalized to midnight),
        ``open``, ``high``, ``low``, ``close``, ``volume``. ``period`` is a
        vendor-style window string (e.g. ``"5y"``, ``"max"``, ``"6mo"``).
        Empty frame when the vendor has no data for the symbol.
        """
        ...
