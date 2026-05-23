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

import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings
from trading_intel.errors import DataSourceError
from trading_intel.greeks.exposures import compute_exposures
from trading_intel.greeks.flip_point import gex_flip

# First-party ConvexValue host. The per-trade time-and-sales view served from
# convexvalue.com/go/tas/ POSTs here with the session cookie — NO runtime/Bearer
# token (that flow only exists for apps on a different origin). Verified live.
_CVX_BASE = "https://convexvalue.com"

# Default tas columns. Valid variants (from the endpoint's own schema error):
# symbol, event_flags, index, time, sequence, exchange_code, price, size,
# bid_price, ask_price, exchange_sale_conditions, trade_through_exempt,
# aggressor_side, spread_leg, extended_trading_hours, valid_tick, tas_type,
# value, spot, gamma, delta, vega, theta, rho, volatility, theo, day.
# NOTE: strike/expiration/opt_kind are NOT columns — they're parsed from `symbol`.
_TAS_COLS = (
    "time",
    "symbol",
    "price",
    "size",
    "value",
    "aggressor_side",
    "spread_leg",
    "tas_type",
    "volatility",
    "delta",
    "gamma",
    "vega",
    "theta",
    "spot",
)

# OCC-ish ConvexValue option symbol: ".SPXW260522C7400" / ".SPX261120P7150".
# leading dot, root letters, YYMMDD, C|P, strike (int or decimal — already in
# real dollars, not OCC's *1000 padding).
_OCC_RE = re.compile(r"^\.?(?P<root>[A-Z]+)(?P<ymd>\d{6})(?P<kind>[CP])(?P<strike>\d+(?:\.\d+)?)$")


def parse_occ_symbol(sym: str) -> tuple[str | None, pd.Timestamp | None, float | None, str | None]:
    """Parse a ConvexValue option symbol → (root, expiration, strike, opt_kind).

    Returns ``(None, None, None, None)`` for anything that doesn't match (e.g. a
    bare underlying tick), so callers can drop or keep non-option prints.
    """
    m = _OCC_RE.match((sym or "").strip())
    if not m:
        return (None, None, None, None)
    root = m.group("root")
    exp = pd.to_datetime(m.group("ymd"), format="%y%m%d", errors="coerce")
    strike = float(m.group("strike"))
    kind = "call" if m.group("kind") == "C" else "put"
    return (root, exp if pd.notna(exp) else None, strike, kind)

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
    "volatility",  # IV
    "oi",
    "day_volume",
    "gxoi",
    "dxoi",
    "vxoi",
    "multiplier",  # contract multiplier (100 for equities/ETFs/SPX)
)

# Structural columns get_chain_as_rows prepends to every row.
_CHAIN_STRUCT = ("symbol", "expiration", "strike", "opt_kind")

# Flow params confirmed valid via probe_param: `value` = cumulative day premium
# traded per strike; buy/sell is bucketed-only (valuebs_5m, ...), not pulled here.
_FLOW_PARAMS = ("volatility", "value", "volm", "oi")

# flowsum params confirmed via probe (volm_buy/sell underscored; greek-OI
# exposures incl. vommaxoi=volga read live regardless of market hours).
_FLOWSUM_PARAMS = (
    "volm_buy",
    "volm_sell",
    "oi",
    "gxoi",
    "dxoi",
    "vxoi",
    "txoi",
    "vannaxoi",
    "vommaxoi",
    "charmxoi",
)

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

    def __init__(self, settings: Settings) -> None:
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
                return self.chain(symbol, exps=tuple(range(1, n + 1)), strike_range=strike_range)
            except DataSourceError as exc:
                last_err = exc
                continue
        # Final fallback: the near-term default.
        if last_err is not None:
            return self.chain(symbol, strike_range=strike_range)
        return self.chain(symbol, strike_range=strike_range)

    def flow_chain(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7, 8),
        strike_range: float = 0.15,
    ) -> pd.DataFrame:
        """Per-strike flow chain: ``premium`` (Convex ``value``), volume, IV, OI.

        ``value`` is cumulative day premium traded per strike. Columns are
        normalized to ``opt_kind``/``premium``/``volume``/``iv`` so the chain feeds
        ``strategies.options_flow.aggregate_flow`` directly.
        """
        rows = self._timed(
            lambda: self._api.get_chain_as_rows(
                symbol, params=list(_FLOW_PARAMS), exps=list(exps), rng=strike_range
            )
        )
        cols = [*_CHAIN_STRUCT, *_FLOW_PARAMS]
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows)
        if df.shape[1] != len(cols):
            raise DataSourceError(
                f"Convex flow chain for {symbol!r} returned {df.shape[1]} columns; "
                f"expected {len(cols)}"
            )
        df.columns = cols
        df = df.rename(columns={"volatility": "iv", "value": "premium", "volm": "volume"})
        df["expiration"] = pd.to_datetime(
            pd.to_numeric(df["expiration"], errors="coerce"),
            unit="D",
            origin="unix",
            errors="coerce",
        )
        return df

    def flow_summary(
        self,
        symbol: str,
        *,
        exps: tuple[int, ...] = tuple(range(1, 13)),
        strike_range: float = 0.20,
    ) -> pd.DataFrame:
        """Per-strike flow + greek-OI exposures (reproduces ConvexValue ``flowsum``).

        Columns: ``opt_kind``, ``expiration``, ``strike``, ``volm_buy``,
        ``volm_sell``, ``oi``, ``gxoi``, ``dxoi``, ``vxoi``, ``txoi``,
        ``vannaxoi``, ``vommaxoi``, ``charmxoi``. Aggregate per expiry with
        ``strategies.options_flow.flowsum_by_expiry``.
        """
        rows = self._timed(
            lambda: self._api.get_chain_as_rows(
                symbol, params=list(_FLOWSUM_PARAMS), exps=list(exps), rng=strike_range
            )
        )
        cols = [*_CHAIN_STRUCT, *_FLOWSUM_PARAMS]
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rows)
        if df.shape[1] != len(cols):
            raise DataSourceError(
                f"Convex flowsum for {symbol!r} returned {df.shape[1]} columns; "
                f"expected {len(cols)}"
            )
        df.columns = cols
        df["expiration"] = pd.to_datetime(
            pd.to_numeric(df["expiration"], errors="coerce"),
            unit="D",
            origin="unix",
            errors="coerce",
        )
        return df

    # ── Time & sales (per-trade prints) ────────────────────────────────
    def time_and_sales(
        self,
        symbol: str,
        *,
        limit: int = 200,
        orderby: str = "value",
        asc: bool = False,
        day: int = 0,
        futs: bool = False,
        filters: list | None = None,
        cols: tuple[str, ...] = _TAS_COLS,
        tz: str = "America/New_York",
    ) -> pd.DataFrame:
        """Per-trade time & sales for ``symbol`` (single sweeps / blocks).

        Hits ``/api/data/tas`` directly through convexlib's authenticated session
        cookie (the same call the convexvalue.com/go/tas/ view makes — no runtime
        token). The response is ``{"data": [<header>, [<rows>]], "meta": {...}}``;
        row values align to ``cols`` order. The OCC ``symbol`` is parsed into
        ``root``/``expiration``/``strike``/``opt_kind``; ``value``→``premium`` and
        ``volatility``→``iv`` to match the flow vocabulary.
        """
        payload = {
            "cols": list(cols),
            "s": [symbol],
            "limit": limit,
            "asc": asc,
            "orderby": orderby,
            "filters": filters if filters is not None else [],
            "day": day,
            "futs": futs,
        }
        resp = self._timed(lambda: self._tas_post(payload))
        data = resp.get("data") if isinstance(resp, dict) else None
        if not data or not isinstance(data, list) or len(data) < 2:
            return pd.DataFrame(columns=[*cols, "root", "expiration", "strike", "opt_kind"])

        header = list(data[0])
        rows = data[1] or []
        df = pd.DataFrame(rows, columns=header)
        if df.empty:
            return pd.DataFrame(columns=[*header, "root", "expiration", "strike", "opt_kind"])

        # Parse the OCC symbol into structured option fields.
        parsed = df["symbol"].map(parse_occ_symbol)
        df["root"] = parsed.map(lambda t: t[0])
        df["expiration"] = parsed.map(lambda t: t[1])
        df["strike"] = parsed.map(lambda t: t[2])
        df["opt_kind"] = parsed.map(lambda t: t[3])

        if "time" in df.columns:
            # tas `time` is epoch-ms in UTC. SPX trades on US exchanges, so default
            # the display to US/Eastern (incl. Cboe's overnight session). tz=None
            # leaves it UTC-naive.
            t = pd.to_datetime(
                pd.to_numeric(df["time"], errors="coerce"), unit="ms", utc=True, errors="coerce"
            )
            df["time"] = t.dt.tz_convert(tz) if tz else t.dt.tz_localize(None)
        df = df.rename(columns={"value": "premium", "volatility": "iv"})
        return df

    def _tas_post(self, payload: dict) -> object:
        """POST the tas payload via convexlib's session cookie. Verified live."""
        sess = self._api.session
        r = sess.post(f"{_CVX_BASE}/api/data/tas", json=payload, timeout=30)
        r.raise_for_status()
        return r.json()

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

    def spot(self, symbol: str) -> float:
        """Public spot accessor (``OptionsDataSource`` Protocol).

        Thin wrapper over the internal price-only lookup so downstream callers
        (e.g. the intraday 0DTE flow collector) can anchor exposure math on the
        underlying price without pulling a full chain.
        """
        return self._spot(symbol)

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

    # ── Generic API escape hatch (for endpoints convexlib doesn't wrap) ──
    def raw_request(self, endpoint: str, *, method: str = "POST", data: object = None) -> object:
        """Call any ConvexValue API endpoint via convexlib's ``make_request``.

        For core endpoints convexlib does not wrap. Keeps all convexlib usage
        inside this client (CLAUDE.md rule 1).
        """
        return self._timed(
            lambda: self._api.make_request(endpoint=endpoint, method=method, data=data)
        )

    # ── Diagnostic: validate a candidate chain field ───────────────────
    def probe_param(
        self, symbol: str, param: str, *, exps: tuple[int, ...] = (1,), rng: float = 0.05
    ) -> tuple[bool, object]:
        """Does Convex accept ``param`` as a chain field? Returns (ok, sample_value).

        Probes a single field so we can discover valid convexlib flow field names
        without risking a 400 on the main pull (one bad param rejects the whole
        request).
        """
        try:
            rows = self._timed(
                lambda: self._api.get_chain_as_rows(
                    symbol, params=[param], exps=list(exps), rng=rng
                )
            )
        except DataSourceError:
            return (False, None)
        if not rows:
            return (True, None)
        row = rows[0]
        return (True, row[-1] if len(row) >= 5 else None)

    # ── Health ─────────────────────────────────────────────────────────
    def health(self) -> dict:
        return {
            "vendor": "convexvalue",
            "last_call_latency_ms": self._health.last_call_latency_ms,
            "last_call_at": self._health.last_call_at,
            "consecutive_failures": self._health.consecutive_failures,
        }

    # ── Internal: latency tracking ─────────────────────────────────────
    def _timed(self, fn: Callable[[], object]) -> object:
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
