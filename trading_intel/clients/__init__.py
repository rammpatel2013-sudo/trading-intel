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

    def exposures(self, symbol: str, exps: tuple[int, ...] = (1, 2, 3)) -> dict:
        """Aggregate GEX/DEX/VEX/CHEX + flip point for one symbol."""
        ...

    def health(self) -> dict:
        """Vendor connectivity, rate-limit status, last call latency."""
        ...
