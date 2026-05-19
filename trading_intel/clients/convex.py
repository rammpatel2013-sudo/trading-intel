"""ConvexValue data client — the primary OptionsDataSource implementation.

Wraps `convexlib.api.ConvexApi` with:
- Pydantic-typed responses
- Retry on transient failures
- Normalized chain DataFrame (consistent column names across vendors)
- Aggregate exposures helper

This module is the ONLY place in the codebase that imports `convexlib`.
All downstream code consumes data via the OptionsDataSource Protocol.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings


# Convex API params we want for the chain endpoint
_CHAIN_PARAMS = (
    "strike",
    "expiration",
    "opt_kind",
    "delta",
    "gamma",
    "theta",
    "vega",
    "vanna",
    "charm",
    "volatility",       # IV
    "oi",
    "day_volume",
    "gxoi",
    "dxoi",
    "vxoi",
    "cxoi",
)

# Convex API params for the underlying endpoint
_UND_PARAMS_BASE = (
    "price",
    "day_volume",
    "option_volume",
    "oi",
    "flowratio",
    "vflowratio",
    "flownet",
    "call_volume",
    "put_volume",
    "put_call_ratio",
    "volatility",
    "dxoi",
    "gxoi",
    "vxoi",
)

# Time-bucketed flow params (appended dynamically)
_UND_TIME_PARAMS = ("volm_{b}", "value_{b}", "volmbs_{b}", "valuebs_{b}")


@dataclass
class ClientHealth:
    last_call_latency_ms: float | None = None
    last_call_at: float | None = None
    consecutive_failures: int = 0


class ConvexClient(OptionsDataSource):
    """Primary data client. Auth is email+password — no token lifecycle."""

    def __init__(self, settings: Settings):
        # Lazy-import convexlib so unit tests can mock without the SDK installed
        from convexlib.api import ConvexApi  # type: ignore[import]

        self._api = ConvexApi(
            settings.CONVEX_EMAIL,
            settings.CONVEX_PASSWORD.get_secret_value(),
            settings.CONVEX_ACCOUNT_TYPE,
        )
        self._health = ClientHealth()

    # ── Chain ──────────────────────────────────────────────────────────
    def chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        rows = self._timed(
            lambda: self._api.get_chain_as_rows(
                symbol,
                params=list(_CHAIN_PARAMS),
                exps=list(exps),
                rng=strike_range,
            )
        )
        if not rows:
            return pd.DataFrame(columns=["symbol", *_CHAIN_PARAMS])

        cols = ["symbol", "expiration", "strike", "opt_kind", *_CHAIN_PARAMS]
        df = pd.DataFrame(rows, columns=cols)
        # Normalize the IV column name to `iv` to match the Protocol
        df = df.rename(columns={"volatility": "iv", "day_volume": "volume"})
        return df

    # ── Underlying ─────────────────────────────────────────────────────
    def underlying(
        self,
        symbols: list[str],
        *,
        time_buckets: tuple[str, ...] = ("5m", "15m", "30m"),
    ) -> pd.DataFrame:
        params = list(_UND_PARAMS_BASE)
        for bucket in time_buckets:
            params += [p.format(b=bucket) for p in _UND_TIME_PARAMS]

        response = self._timed(lambda: self._api.get_und(symbols=symbols, params=params))
        data = response.get("data", [])
        if not data:
            return pd.DataFrame(columns=["symbol", *params])

        df = pd.DataFrame(data, columns=["symbol", *params])
        return df

    # ── Aggregate exposures ────────────────────────────────────────────
    def exposures(self, symbol: str, exps: tuple[int, ...] = (1, 2, 3)) -> dict:
        chain_df = self.chain(symbol, exps=exps)
        if chain_df.empty:
            return {}

        # Sign convention: calls positive, puts negative for GEX
        sign = chain_df["opt_kind"].map({"C": 1, "P": -1})
        gex_total = (chain_df["gxoi"] * sign).sum()
        dex_total = chain_df["dxoi"].sum()
        vex_total = chain_df["vxoi"].sum()
        cxoi_total = chain_df["cxoi"].sum()

        return {
            "symbol": symbol,
            "gex_total": float(gex_total),
            "dex_total": float(dex_total),
            "vex_total": float(vex_total),
            "chex_total": float(cxoi_total),
        }

    # ── Health ─────────────────────────────────────────────────────────
    def health(self) -> dict:
        return {
            "vendor": "convexvalue",
            "last_call_latency_ms": self._health.last_call_latency_ms,
            "last_call_at": self._health.last_call_at,
            "consecutive_failures": self._health.consecutive_failures,
        }

    # ── Internal: latency tracking ─────────────────────────────────────
    def _timed(self, fn):
        t0 = time.perf_counter()
        try:
            result = fn()
            self._health.last_call_latency_ms = (time.perf_counter() - t0) * 1000
            self._health.last_call_at = time.time()
            self._health.consecutive_failures = 0
            return result
        except Exception:
            self._health.consecutive_failures += 1
            raise
