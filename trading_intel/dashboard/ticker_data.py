"""Pure data-prep helpers for the per-ticker dashboard page.

Everything the ``pages/1_Ticker.py`` page needs to draw its panels is computed
here as small, side-effect-free functions so it can be unit-tested without a
running Streamlit (the page itself stays a thin rendering shell).

Two kinds of helper live here:

* **Indicators / aggregations** — pure transforms over price series or a
  per-strike chain (SMA, Bollinger bands, RSI, GEX/DEX-by-strike, a rolling
  average across strikes, and a descriptive normal-distribution fit).
* **DB readers** — thin ``Session`` queries against ``greeks_chain``,
  ``greeks_snapshots`` and ``quotes_daily`` that return tidy DataFrames. These
  are testable against in-memory SQLite (create only the table you need).

FlashAlpha rule (CLAUDE.md rule 4): GEX/DEX/walls/fits here are *regime
descriptors*, not signals. Nothing in this module emits an alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import (
    GreeksChain,
    GreeksSnapshot,
    IntradayFlow,
    QuoteDaily,
)

# ── Price-series indicators ────────────────────────────────────────────


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average of ``close`` over ``window`` periods."""
    return close.rolling(window=window, min_periods=window).mean()


@dataclass(frozen=True)
class Bollinger:
    """Bollinger-band triplet aligned to the input index."""

    mid: pd.Series
    upper: pd.Series
    lower: pd.Series


def bollinger_bands(close: pd.Series, *, window: int = 20, n_std: float = 2.0) -> Bollinger:
    """Bollinger bands: ``window``-SMA midline +/- ``n_std`` population stdevs."""
    mid = close.rolling(window=window, min_periods=window).mean()
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    return Bollinger(mid=mid, upper=mid + n_std * std, lower=mid - n_std * std)


def rsi(close: pd.Series, *, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index of ``close`` (0-100, NaN until primed)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    alpha = 1.0 / window
    avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    out = 100.0 - 100.0 / (1.0 + rs)
    # All-gain windows (avg_loss == 0) saturate to 100; keep the warm-up NaNs.
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(~(avg_gain.isna() | avg_loss.isna()), np.nan)
    return out


# ── Per-strike GEX / DEX aggregations ──────────────────────────────────


def gex_by_strike(chain: pd.DataFrame) -> pd.DataFrame:
    """Net signed gamma-OI per strike (calls +, puts -), ascending by strike.

    ``chain`` needs ``strike``, ``opt_kind`` (call/put) and ``gxoi``. Returns a
    frame with ``strike`` and ``gex`` columns. Empty in → empty out.
    """
    return _signed_by_strike(chain, value_col="gxoi", out_col="gex", apply_sign=True)


def dex_by_strike(chain: pd.DataFrame) -> pd.DataFrame:
    """Net delta-OI per strike, ascending by strike.

    ``dxoi`` already carries the natural call/put sign, so it is summed as-is.
    Needs ``strike`` and ``dxoi``. Empty in → empty out.
    """
    return _signed_by_strike(chain, value_col="dxoi", out_col="dex", apply_sign=False)


def _signed_by_strike(
    chain: pd.DataFrame, *, value_col: str, out_col: str, apply_sign: bool
) -> pd.DataFrame:
    cols = [out_col]
    if chain is None or chain.empty or value_col not in chain.columns:
        return pd.DataFrame(columns=["strike", *cols])
    df = chain.copy()
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    val = pd.to_numeric(df[value_col], errors="coerce").fillna(0.0)
    if apply_sign:
        sign = df["opt_kind"].astype(str).str.upper().str[0].map({"C": 1.0, "P": -1.0})
        val = val * sign.fillna(0.0)
    df = df.assign(_v=val).dropna(subset=["strike"])
    grouped = df.groupby("strike", as_index=False)["_v"].sum()
    grouped = grouped.rename(columns={"_v": out_col}).sort_values("strike")
    return grouped.reset_index(drop=True)


def rolling_avg_by_strike(
    by_strike: pd.DataFrame, value_col: str, *, window: int = 5
) -> pd.Series:
    """Centred rolling mean of ``value_col`` across strikes (smoothing overlay)."""
    if by_strike is None or by_strike.empty or value_col not in by_strike.columns:
        return pd.Series(dtype=float)
    return (
        by_strike[value_col]
        .rolling(window=window, center=True, min_periods=1)
        .mean()
    )


@dataclass(frozen=True)
class NormalFit:
    """A descriptive normal curve fit to a per-strike magnitude distribution."""

    strike: np.ndarray
    fit: np.ndarray
    mean: float
    std: float


def normal_fit_by_strike(
    by_strike: pd.DataFrame, value_col: str, *, n_points: int = 200
) -> NormalFit | None:
    """Fit a magnitude-weighted Gaussian to the by-strike distribution.

    Uses ``|value|`` as the weight to locate the centre/spread of where the
    exposure sits, then returns a Gaussian curve scaled so its peak matches the
    largest absolute bar — purely for visual read-through over the bar chart.
    Returns ``None`` when there is too little signal to fit.
    """
    if by_strike is None or by_strike.empty or value_col not in by_strike.columns:
        return None
    strikes = pd.to_numeric(by_strike["strike"], errors="coerce").to_numpy(dtype=float)
    weights = np.abs(pd.to_numeric(by_strike[value_col], errors="coerce").to_numpy(dtype=float))
    mask = np.isfinite(strikes) & np.isfinite(weights)
    strikes, weights = strikes[mask], weights[mask]
    total = float(weights.sum())
    if strikes.size < 2 or total <= 0.0 or np.unique(strikes).size < 2:
        return None
    mean = float(np.sum(strikes * weights) / total)
    var = float(np.sum(weights * (strikes - mean) ** 2) / total)
    std = float(np.sqrt(var))
    if std <= 0.0:
        return None
    grid = np.linspace(strikes.min(), strikes.max(), n_points)
    curve = np.exp(-0.5 * ((grid - mean) / std) ** 2)
    peak = curve.max()
    if peak > 0:
        curve = curve / peak * weights.max()
    return NormalFit(strike=grid, fit=curve, mean=mean, std=std)


# ── DB readers (testable against in-memory SQLite) ─────────────────────


def _chain_rows_to_frame(rows: list[GreeksChain]) -> pd.DataFrame:
    """Map ``greeks_chain`` ORM rows to a tidy per-strike frame."""
    return pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "expiry": pd.Timestamp(r.expiry) if r.expiry is not None else pd.NaT,
                "gxoi": r.gxoi,
                "dxoi": r.dxoi,
                "oi": r.oi,
                "volume": r.volume,
                "iv": r.iv,
            }
            for r in rows
        ]
    )


def latest_chain_ts(session: Session, symbol: str) -> datetime | None:
    """Timestamp of the most recent ``greeks_chain`` snapshot for ``symbol``."""
    return session.execute(
        select(GreeksChain.ts)
        .where(GreeksChain.symbol == symbol)
        .order_by(GreeksChain.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_latest_chain(session: Session, symbol: str) -> tuple[datetime | None, pd.DataFrame]:
    """Return ``(ts, chain_df)`` for the newest stored chain snapshot.

    ``chain_df`` has ``strike``, ``opt_kind``, ``expiry``, ``gxoi``, ``dxoi``.
    When nothing is stored returns ``(None, empty_frame)``.
    """
    ts = latest_chain_ts(session, symbol)
    if ts is None:
        return None, pd.DataFrame(
            columns=["strike", "opt_kind", "expiry", "gxoi", "dxoi", "oi", "volume", "iv"]
        )
    rows = list(
        session.execute(
            select(GreeksChain).where(GreeksChain.symbol == symbol, GreeksChain.ts == ts)
        ).scalars()
    )
    return ts, _chain_rows_to_frame(rows)


def available_chain_dates(session: Session, symbol: str, *, limit: int = 60) -> list[datetime]:
    """Distinct stored ``greeks_chain`` snapshot timestamps for ``symbol``, newest first."""
    rows = session.execute(
        select(GreeksChain.ts)
        .where(GreeksChain.symbol == symbol)
        .group_by(GreeksChain.ts)
        .order_by(GreeksChain.ts.desc())
        .limit(limit)
    ).scalars()
    return list(rows)


def load_chain_at(session: Session, symbol: str, ts: datetime) -> pd.DataFrame:
    """Per-strike chain frame for ``symbol`` at a specific snapshot ``ts``."""
    rows = list(
        session.execute(
            select(GreeksChain).where(GreeksChain.symbol == symbol, GreeksChain.ts == ts)
        ).scalars()
    )
    return _chain_rows_to_frame(rows)


def near_spot(
    by_strike: pd.DataFrame, spot: float | None, pct: float | None
) -> pd.DataFrame:
    """Filter a by-strike frame to strikes within +/- ``pct`` (fraction) of ``spot``.

    Returns the frame unchanged when ``spot``/``pct`` are missing or it has no
    ``strike`` column (so callers can pass ``pct=None`` for the full chain).
    """
    if (
        by_strike is None
        or by_strike.empty
        or spot is None
        or pct is None
        or spot <= 0
        or "strike" not in by_strike.columns
    ):
        return by_strike
    lo, hi = spot * (1 - pct), spot * (1 + pct)
    strike = pd.to_numeric(by_strike["strike"], errors="coerce")
    return by_strike[(strike >= lo) & (strike <= hi)]


def snapshot_spot_flip(
    snaps: pd.DataFrame, ts: datetime | None = None
) -> tuple[float | None, float | None]:
    """``(spot, gex_flip)`` from the snapshot nearest ``ts`` (latest if ``ts`` is None).

    Lets the ticker page show the spot/flip that belong to the SELECTED chain
    snapshot rather than always the most recent one. Empty history -> ``(None, None)``.
    """
    if snaps is None or snaps.empty:
        return None, None
    if ts is None:
        row = snaps.iloc[-1]
    else:
        idx = (pd.to_datetime(snaps["ts"]) - pd.Timestamp(ts)).abs().idxmin()
        row = snaps.loc[idx]
    spot = pd.to_numeric(row.get("spot"), errors="coerce")
    flip = pd.to_numeric(row.get("gex_flip"), errors="coerce")
    return (
        float(spot) if pd.notna(spot) else None,
        float(flip) if pd.notna(flip) else None,
    )


def load_snapshot_history(session: Session, symbol: str, *, days: int = 180) -> pd.DataFrame:
    """Aggregate ``greeks_snapshots`` time series, oldest first.

    Columns: ``ts, spot, gex_total, dex_total, vex_total, chex_total, gex_flip,
    atm_iv``. ``days`` caps how many of the most recent rows are returned.
    """
    rows = list(
        session.execute(
            select(GreeksSnapshot)
            .where(GreeksSnapshot.symbol == symbol)
            .order_by(GreeksSnapshot.ts.desc())
            .limit(days)
        ).scalars()
    )
    frame = pd.DataFrame(
        [
            {
                "ts": r.ts,
                "spot": r.spot,
                "gex_total": r.gex_total,
                "dex_total": r.dex_total,
                "vex_total": r.vex_total,
                "chex_total": r.chex_total,
                "gex_flip": r.gex_flip,
                "atm_iv": r.atm_iv,
            }
            for r in rows
        ]
    )
    if frame.empty:
        return frame
    return frame.sort_values("ts").reset_index(drop=True)


def latest_snapshot(session: Session, symbol: str) -> GreeksSnapshot | None:
    """Most recent aggregate ``greeks_snapshots`` row for ``symbol`` (or None)."""
    return session.execute(
        select(GreeksSnapshot)
        .where(GreeksSnapshot.symbol == symbol)
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_quotes(session: Session, symbol: str, *, days: int = 250) -> pd.DataFrame:
    """Daily OHLCV from ``quotes_daily`` for ``symbol``, oldest first.

    Columns: ``date, open, high, low, close, volume``. Empty frame when the
    table holds no rows for the symbol (e.g. before the quote collector runs).
    """
    rows = list(
        session.execute(
            select(QuoteDaily)
            .where(QuoteDaily.symbol == symbol)
            .order_by(QuoteDaily.date.desc())
            .limit(days)
        ).scalars()
    )
    frame = pd.DataFrame(
        [
            {
                "date": pd.Timestamp(r.date),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    if frame.empty:
        return frame
    return frame.sort_values("date").reset_index(drop=True)


# ── Intraday 0DTE/1DTE flow readers ────────────────────────────────────

_INTRADAY_VALUE_COLS = (
    "gamma_vol",
    "delta_vol",
    "vanna_vol",
    "charm_vol",
    "gamma_vol_iv",
    "delta_vol_iv",
    "vanna_vol_iv",
    "charm_vol_iv",
    "volume",
    "volume_interval",
)


def _intraday_rows_to_frame(rows: list[IntradayFlow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts": r.ts,
                "expiry": pd.Timestamp(r.expiry) if r.expiry is not None else pd.NaT,
                "dte": r.dte,
                "strike": r.strike,
                "cp": r.cp,
                "spot": r.spot,
                "iv": r.iv,
                "gamma_vol": r.gamma_vol,
                "delta_vol": r.delta_vol,
                "vanna_vol": r.vanna_vol,
                "charm_vol": r.charm_vol,
                "gamma_vol_iv": r.gamma_vol_iv,
                "delta_vol_iv": r.delta_vol_iv,
                "vanna_vol_iv": r.vanna_vol_iv,
                "charm_vol_iv": r.charm_vol_iv,
                "volume": r.volume,
                "volume_interval": r.volume_interval,
            }
            for r in rows
        ]
    )


def latest_intraday_ts(session: Session, symbol: str) -> datetime | None:
    """Timestamp of the newest ``intraday_flow`` snapshot for ``symbol``."""
    return session.execute(
        select(IntradayFlow.ts)
        .where(IntradayFlow.symbol == symbol)
        .order_by(IntradayFlow.ts.desc())
        .limit(1)
    ).scalar_one_or_none()


def load_latest_intraday_flow(
    session: Session, symbol: str
) -> tuple[datetime | None, pd.DataFrame]:
    """Per-strike intraday rows for the newest snapshot of ``symbol``.

    Returns ``(ts, frame)`` where ``frame`` has the per-strike greeks-volume
    columns. The frame is one row per (expiry, strike, side); callers that want
    a single bar per strike should group/sum on ``strike``. ``(None, empty)``
    when nothing is stored.
    """
    ts = latest_intraday_ts(session, symbol)
    if ts is None:
        return None, _intraday_rows_to_frame([])
    rows = list(
        session.execute(
            select(IntradayFlow).where(
                IntradayFlow.symbol == symbol, IntradayFlow.ts == ts
            )
        ).scalars()
    )
    return ts, _intraday_rows_to_frame(rows)


def intraday_by_strike(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse a per-(expiry,strike,side) intraday frame to one row per strike.

    Sums the volume-weighted exposure columns and traded volume across sides and
    kept expiries; carries ``spot`` through. Empty in → empty out.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["strike", *_INTRADAY_VALUE_COLS])
    present = [c for c in _INTRADAY_VALUE_COLS if c in frame.columns]
    grouped = (
        frame.groupby("strike", as_index=False)[present].sum().sort_values("strike")
    )
    return grouped.reset_index(drop=True)


def volume_by_strike_side(frame: pd.DataFrame, *, col: str = "volume") -> pd.DataFrame:
    """Per-strike traded volume split into calls vs puts.

    Returns a frame with ``strike``, ``call`` and ``put`` columns (summed over
    expiries). ``col`` selects cumulative ``volume`` or ``volume_interval``.
    Empty / column-less input yields an empty, correctly-typed frame.
    """
    if (
        frame is None
        or frame.empty
        or col not in frame.columns
        or "cp" not in frame.columns
        or "strike" not in frame.columns
    ):
        return pd.DataFrame(columns=["strike", "call", "put"])
    df = frame.copy()
    df["_side"] = df["cp"].astype(str).str.upper().str[0]
    df["_vol"] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    pivot = df.pivot_table(
        index="strike", columns="_side", values="_vol", aggfunc="sum", fill_value=0.0
    )
    out = pd.DataFrame({"strike": pivot.index})
    out["call"] = pivot["C"].to_numpy() if "C" in pivot.columns else 0.0
    out["put"] = pivot["P"].to_numpy() if "P" in pivot.columns else 0.0
    return out.sort_values("strike").reset_index(drop=True)


def load_intraday_flow_series(
    session: Session, symbol: str, *, day: date | None = None
) -> pd.DataFrame:
    """Aggregate intraday exposure time series (sum per ``ts``), oldest first.

    One row per snapshot timestamp with the summed ``*_vol`` / ``*_vol_iv`` and
    traded-volume columns, for plotting the intraday build of gamma/vanna/charm.
    ``day`` (a ``date``) restricts to a single session; default is all stored.
    """
    stmt = select(IntradayFlow).where(IntradayFlow.symbol == symbol)
    rows = list(session.execute(stmt.order_by(IntradayFlow.ts)).scalars())
    frame = _intraday_rows_to_frame(rows)
    if frame.empty:
        return frame
    if day is not None:
        frame = frame[frame["ts"].apply(lambda t: pd.Timestamp(t).date() == day)]
        if frame.empty:
            return frame.reset_index(drop=True)
    present = [c for c in _INTRADAY_VALUE_COLS if c in frame.columns]
    series = frame.groupby("ts", as_index=False)[present].sum()
    spot = frame.groupby("ts", as_index=False)["spot"].last()
    series = series.merge(spot, on="ts", how="left")
    return series.sort_values("ts").reset_index(drop=True)
