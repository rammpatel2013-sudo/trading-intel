"""Watchlist overview metrics — one descriptive row per ticker.

Aggregates the regime descriptors already collected into a single sortable
table: net GEX (and its direction / weekly change), call/put OI ratio, vol/OI
turnover, ATM skew, the call/put walls and their distance to spot, plus a small
set of *descriptive* gamma-squeeze ingredients (short-dated gamma concentration,
call-wall proximity, dealer gamma regime from the flip point).

FlashAlpha rule (CLAUDE.md rule 4): these are regime descriptors, NOT signals.
Nothing here predicts a squeeze or an "explosive move" — that judgement waits on
the probability model (roadmap C5). The squeeze columns are a read-through only.

Pure functions take plain frames/values (unit-tested); ``load_watchlist_metrics``
is the thin DB orchestrator that reuses the ``ticker_data`` readers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from trading_intel.dashboard.ticker_data import (
    load_latest_chain,
    load_snapshot_history,
)
from trading_intel.errors import ComputationError
from trading_intel.greeks.walls import compute_walls

_CALL = "C"
_PUT = "P"


def _side(chain: pd.DataFrame) -> pd.Series:
    return chain["opt_kind"].astype(str).str.upper().str[0]


def call_put_oi_ratio(chain: pd.DataFrame) -> float | None:
    """Total call OI / total put OI (None if no put OI or missing columns)."""
    if chain is None or chain.empty or "oi" not in chain.columns:
        return None
    side = _side(chain)
    oi = pd.to_numeric(chain["oi"], errors="coerce").fillna(0.0)
    call = float(oi[side == _CALL].sum())
    put = float(oi[side == _PUT].sum())
    return call / put if put > 0 else None


def vol_oi_ratio(chain: pd.DataFrame) -> float | None:
    """Total traded volume / total OI across the chain (turnover)."""
    if chain is None or chain.empty or "volume" not in chain.columns or "oi" not in chain.columns:
        return None
    vol = pd.to_numeric(chain["volume"], errors="coerce").fillna(0.0).sum()
    oi = pd.to_numeric(chain["oi"], errors="coerce").fillna(0.0).sum()
    return float(vol / oi) if oi > 0 else None


def _nearest_expiry(chain: pd.DataFrame) -> pd.DataFrame:
    if "expiry" not in chain.columns or chain["expiry"].isna().all():
        return chain
    nearest = chain["expiry"].min()
    return chain[chain["expiry"] == nearest]


def atm_skew(chain: pd.DataFrame, spot: float, *, wing: float = 0.05) -> float | None:
    """Nearest-expiry skew: mean OTM-put IV minus mean OTM-call IV (decimal).

    Positive = puts richer than calls (the usual equity-index skew). Uses the
    five strikes nearest ``spot*(1-wing)`` (puts) and ``spot*(1+wing)`` (calls).
    """
    if chain is None or chain.empty or "iv" not in chain.columns or not spot or spot <= 0:
        return None
    df = _nearest_expiry(chain).copy()
    df["iv"] = pd.to_numeric(df["iv"], errors="coerce")
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df = df.dropna(subset=["iv", "strike"])
    if df.empty:
        return None
    side = _side(df)
    puts = df[side == _PUT]
    calls = df[side == _CALL]
    if puts.empty or calls.empty:
        return None
    put_iv = _iv_near(puts, spot * (1 - wing))
    call_iv = _iv_near(calls, spot * (1 + wing))
    if put_iv is None or call_iv is None:
        return None
    return float(put_iv - call_iv)


def _iv_near(side_df: pd.DataFrame, target_strike: float, *, n: int = 5) -> float | None:
    distance = (side_df["strike"] - target_strike).abs()
    idx = distance.nsmallest(min(n, len(side_df))).index
    value = float(side_df.loc[idx, "iv"].mean())
    return value if np.isfinite(value) else None


def gamma_concentration(chain: pd.DataFrame, spot: float, *, band: float = 0.03) -> float | None:
    """Fraction of total gamma-OI sitting within ±``band`` of spot (0..1).

    High concentration near spot = a tighter pin / more reflexive hedging zone.
    Descriptive read-through only.
    """
    if chain is None or chain.empty or "gxoi" not in chain.columns or not spot or spot <= 0:
        return None
    gxoi = pd.to_numeric(chain["gxoi"], errors="coerce").fillna(0.0).abs()
    strike = pd.to_numeric(chain["strike"], errors="coerce")
    total = float(gxoi.sum())
    if total <= 0:
        return None
    near = (strike - spot).abs() <= spot * band
    return float(gxoi[near].sum() / total)


def call_wall_distance(call_wall: float | None, spot: float | None) -> float | None:
    """Signed distance from spot to the call wall, as a fraction of spot."""
    if call_wall is None or spot is None or spot <= 0:
        return None
    return float((call_wall - spot) / spot)


def gex_direction(history: pd.DataFrame) -> str:
    """'up' / 'down' / 'flat' / 'n/a' from the last two snapshot gex_total values."""
    if history is None or history.empty or "gex_total" not in history.columns:
        return "n/a"
    vals = pd.to_numeric(history["gex_total"], errors="coerce").dropna()
    if len(vals) < 2:
        return "n/a"
    delta = float(vals.iloc[-1] - vals.iloc[-2])
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "flat"


def gex_change_since(history: pd.DataFrame, *, days: int = 7) -> float | None:
    """Latest gex_total minus the value ~``days`` ago (None if not enough history)."""
    if history is None or history.empty or "gex_total" not in history.columns:
        return None
    df = history.dropna(subset=["gex_total"]).copy()
    if df.empty:
        return None
    df["ts"] = pd.to_datetime(df["ts"])
    latest_ts = df["ts"].max()
    cutoff = latest_ts - pd.Timedelta(days=days)
    prior = df[df["ts"] <= cutoff]
    if prior.empty:
        return None
    return float(df["gex_total"].iloc[-1] - prior["gex_total"].iloc[-1])


def gamma_regime(spot: float | None, gex_flip: float | None) -> str:
    """Dealer gamma regime from spot vs the flip point (descriptive)."""
    if spot is None or gex_flip is None:
        return "n/a"
    if spot < gex_flip:
        return "short gamma (< flip, move-amplifying)"
    if spot > gex_flip:
        return "long gamma (> flip, move-damping)"
    return "at flip"


def build_watchlist_row(
    symbol: str,
    *,
    snapshot: object | None,
    history: pd.DataFrame,
    chain: pd.DataFrame,
    weekly_days: int = 7,
) -> dict:
    """Assemble one descriptive watchlist row from the per-symbol inputs."""
    spot = getattr(snapshot, "spot", None) if snapshot is not None else None
    gex_flip = getattr(snapshot, "gex_flip", None) if snapshot is not None else None
    gex_total = getattr(snapshot, "gex_total", None) if snapshot is not None else None
    atm_iv = getattr(snapshot, "atm_iv", None) if snapshot is not None else None

    walls = {}
    if chain is not None and not chain.empty:
        try:
            walls = compute_walls(chain)
        except ComputationError:
            walls = {}  # a thin/empty chain just yields no walls
    call_wall = walls.get("call_wall")
    put_wall = walls.get("put_wall")

    return {
        "symbol": symbol,
        "spot": spot,
        "gex_total": gex_total,
        "gex_dir": gex_direction(history),
        "gex_chg_wk": gex_change_since(history, days=weekly_days),
        "gamma_regime": gamma_regime(spot, gex_flip),
        "gex_flip": gex_flip,
        "atm_iv": atm_iv,
        "call_put_oi": call_put_oi_ratio(chain),
        "vol_oi": vol_oi_ratio(chain),
        "skew": atm_skew(chain, spot) if spot else None,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_dist": call_wall_distance(call_wall, spot),
        "gamma_conc_3pct": gamma_concentration(chain, spot) if spot else None,
    }


def load_watchlist_metrics(
    session: Session, symbols: list[str], *, weekly_days: int = 7, history_days: int = 30
) -> pd.DataFrame:
    """Build the watchlist metrics table for ``symbols`` (one row each).

    Thin orchestration over the ``ticker_data`` readers — no new queries. Symbols
    with no stored data still produce a row (mostly None) so the table is stable.
    """
    rows: list[dict] = []
    for symbol in symbols:
        history = load_snapshot_history(session, symbol, days=history_days)
        _, chain = load_latest_chain(session, symbol)
        snapshot = _latest_from_history(history)
        rows.append(
            build_watchlist_row(
                symbol, snapshot=snapshot, history=history, chain=chain, weekly_days=weekly_days
            )
        )
    return pd.DataFrame(rows)


class _SnapshotView:
    """Lightweight stand-in exposing the snapshot attributes the row builder reads."""

    def __init__(self, *, spot, gex_total, gex_flip, atm_iv) -> None:  # noqa: ANN001
        self.spot = spot
        self.gex_total = gex_total
        self.gex_flip = gex_flip
        self.atm_iv = atm_iv


def _latest_from_history(history: pd.DataFrame) -> _SnapshotView | None:
    if history is None or history.empty:
        return None
    last = history.iloc[-1]
    return _SnapshotView(
        spot=_opt(last.get("spot")),
        gex_total=_opt(last.get("gex_total")),
        gex_flip=_opt(last.get("gex_flip")),
        atm_iv=_opt(last.get("atm_iv")),
    )


def _opt(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


# ── Display formatting (shared by the Streamlit page + the HTML report) ──

DISPLAY_LABELS = {
    "symbol": "Symbol",
    "spot": "Spot",
    "gex_total": "Net GEX",
    "gex_dir": "GEX dir",
    "gex_chg_wk": "ΔGEX (1wk)",
    "gamma_regime": "Gamma regime",
    "gex_flip": "GEX flip",
    "atm_iv": "ATM IV",
    "call_put_oi": "C/P OI",
    "vol_oi": "Vol/OI",
    "skew": "Skew",
    "call_wall": "Call wall",
    "put_wall": "Put wall",
    "call_wall_dist": "CW dist",
    "gamma_conc_3pct": "gamma-conc +/-3%",
}

_ARROWS = {"up": "up", "down": "down", "flat": "flat", "n/a": "n/a"}
_PCT_COLS = ("atm_iv", "skew", "call_wall_dist", "gamma_conc_3pct")
_NUM_COLS = ("spot", "gex_flip", "call_wall", "put_wall")
_BIG_COLS = ("gex_total", "gex_chg_wk")
_RATIO_COLS = ("call_put_oi", "vol_oi")


def _fmt_pct(v: object) -> str:
    return f"{float(v) * 100:.1f}%" if pd.notna(v) else "n/a"


def _fmt_num(v: object) -> str:
    return f"{float(v):g}" if pd.notna(v) else "n/a"


def _fmt_big(v: object) -> str:
    return f"{float(v):,.0f}" if pd.notna(v) else "n/a"


def _fmt_ratio(v: object) -> str:
    return f"{float(v):.2f}" if pd.notna(v) else "n/a"


def format_display(metrics: pd.DataFrame) -> pd.DataFrame:
    """Human-friendly, display-ready copy of the metrics table (renamed columns).

    Pure: percentages, thousands separators and direction words applied per
    column; missing values rendered as ``"n/a"``. Column order follows
    ``DISPLAY_LABELS``.
    """
    if metrics is None or metrics.empty:
        return pd.DataFrame(columns=list(DISPLAY_LABELS.values()))
    cols = [c for c in DISPLAY_LABELS if c in metrics.columns]
    out = metrics[cols].copy()
    if "gex_dir" in out.columns:
        out["gex_dir"] = out["gex_dir"].map(lambda v: _ARROWS.get(v, v))
    for col in _PCT_COLS:
        if col in out.columns:
            out[col] = out[col].map(_fmt_pct)
    for col in _NUM_COLS:
        if col in out.columns:
            out[col] = out[col].map(_fmt_num)
    for col in _BIG_COLS:
        if col in out.columns:
            out[col] = out[col].map(_fmt_big)
    for col in _RATIO_COLS:
        if col in out.columns:
            out[col] = out[col].map(_fmt_ratio)
    return out.rename(columns=DISPLAY_LABELS)
