"""Pure data-prep for the GEX-by-strike time-series ("surface") page.

Stacks the per-snapshot net-signed-GEX-by-strike profile (calls +, puts -, the
project's GEX convention — MEMORY Formulas) across stored ``greeks_chain``
snapshots into a strike x time matrix for a Convex-style heatmap, plus a
spot/flip overlay read from ``greeks_snapshots``.

Cadence note: ``chain_snapshot`` runs once daily, so this yields a
daily-resolution series (one column per trading day). Intraday resolution would
need a heavier intraday chain collector — a deliberate follow-up, not this.

Everything here is side-effect-free and unit-testable against in-memory SQLite
(create only ``greeks_chain`` / ``greeks_snapshots``). Per the FlashAlpha rule
(CLAUDE.md rule 4) the GEX surface is a *regime descriptor*, not a signal —
nothing in this module emits an alert.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.dashboard.ticker_data import (
    _chain_rows_to_frame,
    gex_by_strike,
    load_snapshot_history,
)
from trading_intel.memory.models import GreeksChain

_LONG_COLS = ["ts", "strike", "net_gex"]
_OVERLAY_COLS = ["ts", "spot", "gex_flip"]


def _recent_chain_ts(session: Session, symbol: str, *, days: int) -> list[datetime]:
    """Distinct ``greeks_chain`` timestamps for ``symbol`` within ``days``, oldest first.

    Bounds the scan by both a row count (``days`` distinct snapshots, daily
    cadence) and an absolute cutoff so a stale gap can't drag in old columns.
    """
    rows = list(
        session.execute(
            select(GreeksChain.ts)
            .where(GreeksChain.symbol == symbol)
            .distinct()
            .order_by(GreeksChain.ts.desc())
            .limit(days)
        ).scalars()
    )
    if not rows:
        return []
    cutoff = max(rows) - timedelta(days=days)
    return sorted(ts for ts in rows if ts >= cutoff)


def _expiry_within(chain: pd.DataFrame, ts: datetime, expiry_within_days: int) -> pd.DataFrame:
    """Keep only strikes whose expiry is within ``expiry_within_days`` DTE of ``ts``.

    Compares calendar dates (not timestamps) so it is robust whether ``ts`` is
    tz-aware (Postgres) or tz-naive (SQLite) — subtracting a tz-naive expiry from
    a tz-aware ts would otherwise raise.
    """
    if chain.empty or "expiry" not in chain.columns:
        return chain
    ref = pd.Timestamp(ts).date()
    dte = pd.to_numeric(
        chain["expiry"].map(
            lambda e: (pd.Timestamp(e).date() - ref).days if pd.notna(e) else None
        ),
        errors="coerce",
    )
    mask = (dte >= 0) & (dte <= expiry_within_days)
    return chain[mask.fillna(False)]


def _spot_by_date(session: Session, symbol: str, *, days: int) -> dict[date, float]:
    """Map each snapshot day to its spot, for the spot-relative strike window."""
    hist = load_snapshot_history(session, symbol, days=days)
    if hist.empty:
        return {}
    out: dict[date, float] = {}
    for ts, spot in zip(hist["ts"], hist["spot"], strict=False):
        if spot is not None and not pd.isna(spot):
            out[pd.Timestamp(ts).date()] = float(spot)
    return out


def load_gex_strike_series(
    session: Session,
    symbol: str,
    *,
    days: int = 30,
    expiry_within_days: int | None = None,
    pct_range: float | None = 0.03,
) -> pd.DataFrame:
    """Net signed GEX by strike for each stored chain snapshot in range, oldest first.

    Returns a tidy long frame with columns ``ts``, ``strike``, ``net_gex``
    (calls +, puts -). Optionally restricts each snapshot to strikes expiring
    within ``expiry_within_days`` of that snapshot (a near-term gamma view).
    ``pct_range`` (default 0.03 = +/-3% of that day's spot) trims the chain to a
    near-the-money band so the heatmap focuses on the active strikes instead of
    the full +/-15% pull; pass ``None`` to keep every strike. The spot per day
    comes from ``greeks_snapshots``; days with no stored spot keep all strikes.
    Empty frame when no chain snapshots are stored for ``symbol``.
    """
    ts_list = _recent_chain_ts(session, symbol, days=days)
    if not ts_list:
        return pd.DataFrame(columns=_LONG_COLS)

    rows = list(
        session.execute(
            select(GreeksChain).where(
                GreeksChain.symbol == symbol, GreeksChain.ts.in_(ts_list)
            )
        ).scalars()
    )
    frame = _chain_rows_to_frame(rows)
    if frame.empty:
        return pd.DataFrame(columns=_LONG_COLS)
    frame = frame.assign(ts=[r.ts for r in rows])

    spot_by_date = _spot_by_date(session, symbol, days=days) if pct_range else {}

    parts: list[pd.DataFrame] = []
    for ts in ts_list:
        chain = frame[frame["ts"] == ts]
        if expiry_within_days is not None:
            chain = _expiry_within(chain, ts, expiry_within_days)
        by_strike = gex_by_strike(chain)
        if pct_range:
            spot = spot_by_date.get(pd.Timestamp(ts).date())
            if spot and spot > 0:
                lo, hi = spot * (1.0 - pct_range), spot * (1.0 + pct_range)
                by_strike = by_strike[(by_strike["strike"] >= lo) & (by_strike["strike"] <= hi)]
        if by_strike.empty:
            continue
        parts.append(by_strike.assign(ts=ts).rename(columns={"gex": "net_gex"}))

    if not parts:
        return pd.DataFrame(columns=_LONG_COLS)
    out = pd.concat(parts, ignore_index=True)
    return out[_LONG_COLS].sort_values(["ts", "strike"]).reset_index(drop=True)


def gex_strike_matrix(series: pd.DataFrame) -> pd.DataFrame:
    """Pivot the long GEX series to ``index=strike, columns=ts, values=net_gex``.

    Strikes are reindexed to the sorted union across all snapshots so a strike
    missing from one column shows as NaN (a gap) rather than collapsing rows.
    Empty in -> empty out.
    """
    if series is None or series.empty:
        return pd.DataFrame()
    matrix = series.pivot_table(
        index="strike", columns="ts", values="net_gex", aggfunc="sum"
    )
    return matrix.sort_index().sort_index(axis=1)


def spot_flip_overlay(session: Session, symbol: str, *, days: int = 30) -> pd.DataFrame:
    """``[ts, spot, gex_flip]`` from ``greeks_snapshots`` for the overlay lines.

    Thin projection over ``ticker_data.load_snapshot_history``; oldest first.
    Empty frame when no aggregate snapshots are stored.
    """
    hist = load_snapshot_history(session, symbol, days=days)
    if hist.empty:
        return pd.DataFrame(columns=_OVERLAY_COLS)
    return hist[_OVERLAY_COLS].reset_index(drop=True)


# ── Latest snapshot — rich per-strike frame for the 4-profile panel ────


def load_latest_chain_rich(
    session: Session, symbol: str
) -> tuple[datetime | None, pd.DataFrame]:
    """Latest ``greeks_chain`` snapshot for ``symbol``, rich-column variant.

    Unlike ``ticker_data._chain_rows_to_frame`` (which drops ``vanna`` / ``delta``
    for the slim downstream views), this frame carries every column the
    4-profile panel needs: ``strike, opt_kind, expiry, oi, gxoi, dxoi, vanna,
    delta``. Returns ``(ts, frame)`` — ``(None, empty)`` when nothing is stored.
    """
    ts = session.execute(
        select(GreeksChain.ts)
        .where(GreeksChain.symbol == symbol)
        .order_by(GreeksChain.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None, pd.DataFrame(
            columns=["strike", "opt_kind", "expiry", "oi", "gxoi", "dxoi", "vxoi", "delta"]
        )
    rows = list(
        session.execute(
            select(GreeksChain).where(GreeksChain.symbol == symbol, GreeksChain.ts == ts)
        ).scalars()
    )
    frame = pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "expiry": pd.Timestamp(r.expiry) if r.expiry is not None else pd.NaT,
                "oi": r.oi,
                "gxoi": r.gxoi,
                "dxoi": r.dxoi,
                "vxoi": r.vxoi,
                "delta": r.delta,
            }
            for r in rows
        ]
    )
    return ts, frame


def latest_strike_profiles(
    session: Session,
    symbol: str,
    *,
    pct_range: float | None = 0.03,
) -> pd.DataFrame:
    """One frame, four metrics: ``[strike, oi, gex, vanna, delta]`` for the latest snapshot.

    Convenience wrapper for the 4-profile panel — pulls the rich chain via
    :func:`load_latest_chain_rich`, runs :func:`aggregate_by_strike` for each
    kind, and outer-joins on strike. ``pct_range`` trims to ±x% of the
    snapshot spot (looked up via :func:`load_snapshot_history`) — pass ``None``
    to keep every strike. Empty in → empty out.
    """
    cols = ["strike", "oi", "gex", "vanna", "delta"]
    _, chain = load_latest_chain_rich(session, symbol)
    if chain.empty:
        return pd.DataFrame(columns=cols)

    if pct_range is not None and pct_range > 0:
        hist = load_snapshot_history(session, symbol, days=1)
        spot = None
        if not hist.empty and pd.notna(hist["spot"].iloc[-1]):
            spot = float(hist["spot"].iloc[-1])
        if spot and spot > 0:
            lo, hi = spot * (1.0 - pct_range), spot * (1.0 + pct_range)
            chain = chain[(chain["strike"] >= lo) & (chain["strike"] <= hi)]
            if chain.empty:
                return pd.DataFrame(columns=cols)

    parts = []
    for kind, label in (("oi", "oi"), ("gex", "gex"), ("vanna", "vanna"), ("delta", "delta")):
        agg = aggregate_by_strike(chain, kind).rename(columns={"value": label})
        parts.append(agg)

    out = parts[0]
    for part in parts[1:]:
        out = out.merge(part, on="strike", how="outer")
    return out[cols].sort_values("strike").reset_index(drop=True).fillna(0.0)


def aggregate_by_strike(chain: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Per-strike aggregation for the 4-profile panel.

    Returns a tidy frame ``[strike, value]`` ascending by strike. ``kind``:

    - ``"oi"``     — unsigned sum of resting open interest
    - ``"gex"``    — net signed gamma OI (calls +, puts -); sums ``gxoi`` × sign
    - ``"vanna"``  — net signed vanna OI; ``vanna`` × ``oi`` × sign
    - ``"delta"``  — sum of ``dxoi`` (carries the natural call/put sign already)

    Empty frame in → empty frame out.
    """
    cols = ["strike", "value"]
    if chain is None or chain.empty or "strike" not in chain.columns:
        return pd.DataFrame(columns=cols)
    df = chain.copy()
    sign = (
        df["opt_kind"].astype(str).str[0].str.upper()
        .map({"C": 1.0, "P": -1.0}).fillna(0.0)
    )
    if kind == "oi":
        df["_v"] = pd.to_numeric(df["oi"], errors="coerce").fillna(0.0)
    elif kind == "gex":
        df["_v"] = pd.to_numeric(df["gxoi"], errors="coerce").fillna(0.0) * sign
    elif kind == "vanna":
        # Convex pre-computes vanna×OI as ``vxoi`` (mirrors gxoi). Sum signed.
        df["_v"] = pd.to_numeric(df["vxoi"], errors="coerce").fillna(0.0) * sign
    elif kind == "delta":
        df["_v"] = pd.to_numeric(df["dxoi"], errors="coerce").fillna(0.0)
    else:
        raise ValueError(f"unknown kind: {kind!r}")
    out = (
        df.groupby("strike", as_index=False)["_v"].sum()
        .rename(columns={"_v": "value"})
        .sort_values("strike")
        .reset_index(drop=True)
    )
    return out[cols]
