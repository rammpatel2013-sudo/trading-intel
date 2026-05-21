"""ConvexValue data client — the primary OptionsDataSource implementation.

This module is the ONLY place in the codebase that imports `convexlib`.
All downstream code consumes data via the OptionsDataSource Protocol.

Field-code reference: https://github.com/convexvalue/convexlib (README lists the
valid get_und and get_chain params). Two response-shape notes learned against
the live API (the README is slightly out of date):
- ``get_chain_as_rows`` returns each option as
  ``[symbol, expiration, strike, kind, *params]`` — symbol/expiration/strike/
  kind are structural and must NOT be requested as params.
- ``get_und`` nests rows one level deeper than the README example:
  ``{"data": [[ [symbol, *vals], ... ]]}`` — the rows live at ``data[0]``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings
from trading_intel.errors import DataSourceError
from trading_intel.greeks.exposures import compute_exposures
from trading_intel.greeks.flip_point import gex_flip


# Chain DATA params only — symbol/expiration/strike/kind are added structurally
# by get_chain_as_rows. Every code below is a valid get_chain param per the
# convexlib README. (There is NO `cxoi`; we recompute charm/vanna exposure from
# the raw `charm`/`vanna` greeks.)
_CHAIN_PARAMS = (
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
    "multiplier",       # contract multiplier (100 for equities/ETFs/SPX)
)

# Structural columns get_chain_as_rows prepends to every row.
_CHAIN_STRUCT = ("symbol", "expiration", "strike", "opt_kind")

# get_und params for the full underlying snapshot (all valid per README).
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
        cols = [*_CHAIN_STRUCT, *_CHAIN_PARAMS]
        if not rows:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(rows)
        if df.shape[1] != len(cols):
            raise DataSourceError(
                f"Convex chain for {symbol!r} returned {df.shape[1]} columns; "
                f"expected {len(cols)} ([symbol, expiration, strike, kind, "
                f"*{len(_CHAIN_PARAMS)} params])"
            )
        df.columns = cols
        # Normalize names to the OptionsDataSource Protocol vocabulary.
        df = df.rename(columns={"volatility": "iv", "day_volume": "volume"})
        # Convex returns `expiration` as days since the Unix epoch (e.g. 20595 =
        # 2026-05-22). Normalize to a real datetime so the greeks layer stays
        # vendor-agnostic.
        df["expiration"] = pd.to_datetime(
            pd.to_numeric(df["expiration"], errors="coerce"),
            unit="D",
            origin="unix",
            errors="coerce",
        )
        return df

    def chain_long(
        self,
        symbol: str,
        *,
        max_exps: int = 40,
        strike_range: float = 0.20,
    ) -> pd.DataFrame:
        """Chain spanning many expirations (for long-dated / rolling GEX).

        Convex's ``exps`` are expiration indices; we don't know up front how
        many a symbol has, so we request a wide range and fall back to fewer if
        the vendor rejects the request. The caller filters by expiration date.
        """
        candidates = (max_exps, 30, 20, 12)
        last_err: DataSourceError | None = None
        for n in candidates:
            try:
                return self.chain(
                    symbol, exps=tuple(range(1, n + 1)), strike_range=strike_range
                )
            except DataSourceError as exc:
                last_err = exc
                continue
        # Final fallback: the near-term default.
        if last_err is not None:
            return self.chain(symbol, strike_range=strike_range)
        return self.chain(symbol, strike_range=strike_range)

    # ── Underlying ─────────────────────────────────────────────────────
    def _get_und(self, symbols: list[str], params: list[str]) -> pd.DataFrame:
        """Call get_und and shape the response defensively.

        The live API nests rows one level deeper than the README example
        (``data == [[ [symbol, *vals], ... ]]``), so we unwrap ``data[0]``. We
        also never force a fixed column count — Convex may omit a param it does
        not recognize — so callers that only need ``price``/``symbol`` still work.
        """
        response = self._timed(
            lambda: self._api.get_und(symbols=list(symbols), params=list(params))
        )
        raw = response.get("data", []) if isinstance(response, dict) else []
        if not raw:
            return pd.DataFrame(columns=["symbol", *params])

        # Unwrap the extra nesting level when present (data[0] holds the rows).
        rows = raw
        if isinstance(raw[0], list) and raw[0] and isinstance(raw[0][0], list):
            rows = raw[0]
        if not rows:
            return pd.DataFrame(columns=["symbol", *params])

        df = pd.DataFrame(rows)
        expected = ["symbol", *params]
        width = df.shape[1]
        if width == len(expected):
            df.columns = expected
        elif width < len(expected):
            # Convex omitted some trailing params; name what we got (symbol first).
            df.columns = expected[:width]
        else:
            df.columns = expected + [f"extra_{i}" for i in range(width - len(expected))]
        return df

    def underlying(
        self,
        symbols: list[str],
        *,
        time_buckets: tuple[str, ...] = (),
    ) -> pd.DataFrame:
        # NOTE: time-bucketed flow (volm_5m, ...) are CHAIN params, not get_und
        # params, so time_buckets is accepted for Protocol compatibility but is
        # not used here. Bucketed flow lives on the chain endpoint.
        return self._get_und(list(symbols), list(_UND_PARAMS_BASE))

    def _spot(self, symbol: str) -> float:
        """Fetch the current underlying price (price-only — minimal & proven)."""
        und = self._get_und([symbol], ["price"])
        if und.empty or "price" not in und.columns:
            raise DataSourceError(f"No underlying price returned for {symbol!r}")
        spot = pd.to_numeric(und["price"].iloc[0], errors="coerce")
        if not pd.notna(spot) or spot <= 0:
            raise DataSourceError(f"Invalid spot for {symbol!r}: {spot!r}")
        return float(spot)

    # ── Aggregate exposures ────────────────────────────────────────────
    def exposures(self, symbol: str, exps: tuple[int, ...] = (1, 2, 3)) -> dict:
        """Aggregate GEX/DEX/VEX/CHEX + flip point for one symbol.

        Delegates the math to ``greeks/`` so the locked formulas live in one
        place and stay vendor-agnostic. This client only fetches/shapes data.
        """
        chain_df = self.chain(symbol, exps=exps)
        if chain_df.empty:
            return {}

        spot = self._spot(symbol)
        result = compute_exposures(chain_df, spot)
        if not result:
            return {}

        result["symbol"] = symbol
        result["spot"] = spot
        result["gex_flip"] = gex_flip(chain_df, spot)
        return result

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
        except Exception as exc:
            # Catch vendor failures at the clients/ boundary and re-raise as a
            # domain error with the original attached (CLAUDE.md → Errors).
            self._health.consecutive_failures += 1
            raise DataSourceError(f"ConvexValue API call failed: {exc}") from exc
