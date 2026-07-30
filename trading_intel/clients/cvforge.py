"""CVForge data client - the SECONDARY OptionsDataSource (ADR-004).

CVForge is ConvexValue's AI-tooling product on the SAME backend as convexlib,
exposed as a keyed REST API (``cv_live_...``). ``convex.py`` remains the PRIMARY
source for the live regime engine (rule 1 unchanged); this client covers what
the 7/min convexlib cap can't reach: market-wide breadth (``/screen``,
``/query``), historical option/stock OHLC (``/mas``), and the 157 FMP endpoints.

Like ``convex.py``, this is the ONLY place CVForge HTTP is spoken - all
downstream code consumes the normalized DataFrames. CVForge's snapshot ships
first-order greeks only (delta/gamma/theta/vega + IV), so ``chain()``
SYNTHESIZES ``vanna``/``charm`` (and gxoi/dxoi/vxoi) via ``greeks.black_scholes``
so the frame feeds ``greeks.exposures.compute_exposures`` unchanged
(ADR-004, extending the ADR-002 recompute precedent).

Absolute VEX/CHEX scale vs convexlib's native ``vanna``/``charm`` is
calibration-pending (ADR-004 consequences); GEX/DEX are well-defined.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx
import pandas as pd

from trading_intel.config import Settings
from trading_intel.errors import DataSourceError
from trading_intel.greeks.black_scholes import bs_charm, bs_vanna, years_to_expiry

# Per-contract fields pulled for a chain: the greeks/IV/OI/vol we need to
# synthesize the convex-equivalent exposure columns. Order is echoed back in the
# response ``params`` and drives the positional row layout.
_CHAIN_FIELDS: tuple[str, ...] = (
    "ticker",
    "expiration_date",
    "strike_price",
    "contract_type",
    "delta",
    "gamma",
    "theta",
    "vega",
    "implied_volatility",
    "open_interest",
    "day_volume",
    "underlying_price",
)


class CVForgeClient:
    """Secondary data client (CVForge REST). Auth: ``cv_live_...`` bearer key."""

    def __init__(self, settings: Settings, *, timeout: float = 30.0) -> None:
        key = settings.CVFORGE_API_KEY.get_secret_value()
        if not key:
            raise DataSourceError("CVFORGE_API_KEY is not set in .env")
        self._client = httpx.Client(
            base_url=settings.CVFORGE_BASE_URL.rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        self._last_latency_ms: float | None = None

    # ── low-level HTTP (all CVForge traffic funnels through here) ───────
    def _request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> Any:  # noqa: ANN401 (parsed JSON is dynamically typed)
        t0 = time.perf_counter()
        try:
            resp = self._client.request(method, path, params=params, json=json)
            resp.raise_for_status()
            self._last_latency_ms = (time.perf_counter() - t0) * 1000
            return resp.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            raise DataSourceError(
                f"CVForge {method} {path} -> {exc.response.status_code}: {body}"
            ) from exc
        except httpx.HTTPError as exc:
            raise DataSourceError(f"CVForge {method} {path} failed: {exc}") from exc

    def _get(self, path: str, params: dict | None = None) -> Any:  # noqa: ANN401
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict) -> Any:  # noqa: ANN401
        return self._request("POST", path, json=json)

    # ── chain (normalized, greeks synthesized) ─────────────────────────
    def chain(self, symbol: str, *, ref: date | None = None) -> pd.DataFrame:
        """Full normalized chain for one underlying, greeks synthesized.

        Columns are compatible with ``greeks.exposures.compute_exposures``:
        ``opt_kind, strike, expiration, delta, gamma, theta, vega, iv, oi,
        volume, vanna, charm (per-day), gxoi, dxoi, vxoi, oi_change (NaN),
        underlying_price``. Empty frame if CVForge returns no contracts.
        """
        data = self._get(f"/chains/{symbol.upper()}", {"params": ",".join(_CHAIN_FIELDS)})
        rows = _flatten_chain(data)
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        for col in (
            "strike",
            "delta",
            "gamma",
            "theta",
            "vega",
            "iv",
            "oi",
            "volume",
            "underlying_price",
        ):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["expiration"] = pd.to_datetime(df["expiration"], errors="coerce")
        df["opt_kind"] = df["opt_kind"].astype(str)
        df = df.dropna(subset=["strike", "iv", "underlying_price"])
        # Drop non-positive AND absurd IVs: CVForge occasionally returns garbage
        # near-money rows (seen: XLF ~10.0 = 1000%) that would corrupt ATM IV /
        # skew / the strike-IV grid. 5.0 (500%) is far above any real ETF vol.
        df = df[(df["iv"] > 0) & (df["iv"] < 5.0)].reset_index(drop=True)
        if df.empty:
            return df

        spot = float(df["underlying_price"].iloc[0])
        t = years_to_expiry(df["expiration"], ref or date.today())
        strike = df["strike"].to_numpy(dtype=float)
        sigma = df["iv"].to_numpy(dtype=float)
        oi = df["oi"].fillna(0.0).to_numpy(dtype=float)

        # Synthesize the greeks CVForge omits (ADR-004). charm /365 -> per-day so
        # exposures.CHEX (which x365) annualizes back, matching convexlib.
        df["vanna"] = bs_vanna(spot, strike, sigma, t)
        df["charm"] = bs_charm(spot, strike, sigma, t) / 365.0
        # convex-equivalent greek*OI columns. delta is already call/put signed, so
        # dxoi carries its natural sign (matches convex.py's dxoi convention).
        df["gxoi"] = df["gamma"].fillna(0.0).to_numpy(dtype=float) * oi
        df["dxoi"] = df["delta"].fillna(0.0).to_numpy(dtype=float) * oi
        df["vxoi"] = df["vega"].fillna(0.0).to_numpy(dtype=float) * oi
        df["oi_change"] = float("nan")  # CVForge has no day-over-day OI change
        return df

    def spot(self, symbol: str) -> float:
        """Current underlying price via the FMP quote endpoint (light)."""
        quote = self.fmp("quote", {"symbol": symbol.upper()})
        if isinstance(quote, list) and quote and quote[0].get("price"):
            return float(quote[0]["price"])
        raise DataSourceError(f"No CVForge/FMP price for {symbol!r}")

    def exposures(
        self, symbol: str, *, ref: date | None = None, chain: pd.DataFrame | None = None
    ) -> dict:
        """Aggregate GEX/DEX/VEX/CHEX + ATM IV for one symbol (delegates to greeks/).

        Pass a pre-fetched ``chain`` (from :meth:`chain`) to reuse one pull when the
        caller also needs the raw frame, avoiding a redundant ``/chains`` round-trip.
        """
        from trading_intel.greeks.exposures import compute_exposures

        df = self.chain(symbol, ref=ref) if chain is None else chain
        if df.empty:
            return {}
        spot = float(df["underlying_price"].iloc[0])
        result = compute_exposures(df, spot)
        if result:
            from trading_intel.greeks.exposures import positioning_extras
            from trading_intel.greeks.flip_point import gex_flip

            result["symbol"] = symbol.upper()
            result["spot"] = spot
            result["gex_flip"] = gex_flip(df, spot)  # parity with convex.py (sector flip cushion)
            result.update(positioning_extras(df, spot))
        return result

    # ── historical OHLC (stock or option ticker) - Research plan ───────
    def aggs(
        self,
        ticker: str,
        *,
        frm: str,
        to: str,
        multiplier: int = 1,
        timespan: str = "day",
        limit: int = 5000,
    ) -> pd.DataFrame:
        """Historical OHLC bars for a stock (``AAPL``) or option (``O:...``) ticker."""
        path = f"/mas/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{frm}/{to}"
        data = self._get(path, {"limit": limit})
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v"])
        df = pd.DataFrame(results)
        if "t" in df.columns:
            df["ts"] = pd.to_datetime(df["t"], unit="ms")
        return df

    # ── FMP passthrough (157 endpoints) ────────────────────────────────
    def fmp(self, endpoint: str, params: dict | None = None) -> Any:  # noqa: ANN401
        """Call any of the allowlisted FMP ``/stable`` endpoints on the same key."""
        return self._get(f"/fmp/stable/{endpoint.lstrip('/')}", params or {})

    # ── market-wide breadth (the 7/min cap can't reach these) ──────────
    def screen(
        self,
        *,
        columns: list[str],
        filters: list[dict],
        sort: list[dict] | None = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Cross-market screener over every contract. Ops: gt/gte/lt/lte/eq/ne (+ _field)."""
        body: dict[str, Any] = {"columns": list(columns), "filters": list(filters), "limit": limit}
        if sort:
            body["sort"] = list(sort)
        data = self._post("/screen", body)
        return pd.DataFrame(data.get("rows", []), columns=data.get("columns", columns))

    def query(self, sql: str, *, max_rows: int = 10_000) -> pd.DataFrame:
        """Read-only DuckDB SQL over the live snapshot (table ``options_snapshots``)."""
        data = self._post("/query", {"sql": sql, "max_rows": max_rows})
        return pd.DataFrame(data.get("rows", []))

    def health(self) -> dict:
        return {"vendor": "cvforge", "last_call_latency_ms": self._last_latency_ms}

    def close(self) -> None:
        self._client.close()


def _flatten_chain(data: dict) -> list[dict]:
    """Flatten CVForge's grouped ``/chains`` response into per-contract dicts.

    Response shape: ``{params:[...], chain:[{expiration, strikes:[[strike, row,
    row], ...]}]}`` where each *row* is a positional array in ``params`` order.
    """
    params = data.get("params") or list(_CHAIN_FIELDS)
    idx = {name: i for i, name in enumerate(params)}

    def g(row: list, key: str) -> Any:  # noqa: ANN401
        i = idx.get(key)
        return row[i] if i is not None and i < len(row) else None

    out: list[dict] = []
    for grp in data.get("chain", []):
        for strike_row in grp.get("strikes", []):
            for row in strike_row[1:]:  # [0] is the strike float; [1:] are call/put rows
                if not isinstance(row, list):
                    continue
                out.append(
                    {
                        "ticker": g(row, "ticker"),
                        "expiration": g(row, "expiration_date"),
                        "strike": g(row, "strike_price"),
                        "opt_kind": g(row, "contract_type"),
                        "delta": g(row, "delta"),
                        "gamma": g(row, "gamma"),
                        "theta": g(row, "theta"),
                        "vega": g(row, "vega"),
                        "iv": g(row, "implied_volatility"),
                        "oi": g(row, "open_interest"),
                        "volume": g(row, "day_volume"),
                        "underlying_price": g(row, "underlying_price"),
                    }
                )
    return out
