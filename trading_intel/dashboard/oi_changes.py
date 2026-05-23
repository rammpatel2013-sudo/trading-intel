"""Day-over-day positioning analytics over the wide EOD chain (``oi_chain_eod``).

Diffs the two most recent end-of-day per-strike snapshots for a symbol to
surface, per strike: change in open interest (our own ΔOI = today - yesterday,
cross-checked against Convex's native ``oi_change``), today's traded volume, how
much of that volume "stuck" as OI (``conversion`` = |ΔOI| / volume - new
positioning vs day-trade churn), and each strike's net-signed GEX contribution
(calls +, puts -, the project convention) plus its day-over-day change. Rolls up
to total ΔGEX and call-vs-put ΔOI.

These are descriptive regime lenses to help read positioning — NOT signals
(FlashAlpha rule 4). Nothing here emits an alert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import OiChainEod

_KEYS = ["expiry", "strike", "cp"]
_FRAME_COLS = [
    "expiry", "strike", "cp",
    "oi_prev", "oi_curr", "d_oi", "oi_change_vendor",
    "volume", "conversion",
    "gex_contrib_prev", "gex_contrib_curr", "d_gex_contrib",
]


def _rows_to_frame(rows: list[OiChainEod]) -> pd.DataFrame:
    """Map ``oi_chain_eod`` ORM rows to a tidy per-strike frame (signed GEX added)."""
    df = pd.DataFrame(
        [
            {
                "expiry": pd.Timestamp(r.expiry).date() if r.expiry is not None else None,
                "strike": r.strike,
                "cp": "C" if str(r.cp).upper().startswith("C") else "P",
                "oi": r.oi,
                "oi_change": r.oi_change,
                "volume": r.volume,
                "gxoi": r.gxoi,
            }
            for r in rows
        ]
    )
    if df.empty:
        return df
    sign = df["cp"].map({"C": 1.0, "P": -1.0}).fillna(0.0)
    df["gex_contrib"] = pd.to_numeric(df["gxoi"], errors="coerce").fillna(0.0) * sign
    return df


def load_recent_eod(
    session: Session, symbol: str, *, n: int = 2
) -> list[tuple[datetime, pd.DataFrame]]:
    """Latest ``n`` distinct EOD snapshots for ``symbol``, newest first.

    Each item is ``(ts, frame)`` with per-strike ``oi``/``oi_change``/``volume``/
    ``gxoi`` plus a signed ``gex_contrib`` column.
    """
    ts_list = list(
        session.execute(
            select(OiChainEod.ts)
            .where(OiChainEod.symbol == symbol)
            .distinct()
            .order_by(OiChainEod.ts.desc())
            .limit(n)
        ).scalars()
    )
    snaps: list[tuple[datetime, pd.DataFrame]] = []
    for ts in ts_list:
        rows = list(
            session.execute(
                select(OiChainEod).where(
                    OiChainEod.symbol == symbol, OiChainEod.ts == ts
                )
            ).scalars()
        )
        snaps.append((ts, _rows_to_frame(rows)))
    return snaps


def build_oi_change_frame(prev: pd.DataFrame, curr: pd.DataFrame) -> pd.DataFrame:
    """Per-strike day-over-day diff frame from a previous and current snapshot.

    Outer-joins on (expiry, strike, cp) keeping every strike present today; a
    strike new today (absent yesterday) is treated as OI 0 -> today (full ΔOI).
    Empty frame when ``curr`` is empty.
    """
    if curr is None or curr.empty:
        return pd.DataFrame(columns=_FRAME_COLS)
    prev = prev if prev is not None and not prev.empty else pd.DataFrame(columns=curr.columns)

    p = prev[[*_KEYS, "oi", "gex_contrib"]].rename(
        columns={"oi": "oi_prev", "gex_contrib": "gex_contrib_prev"}
    )
    c = curr[[*_KEYS, "oi", "oi_change", "volume", "gex_contrib"]].rename(
        columns={
            "oi": "oi_curr", "oi_change": "oi_change_vendor",
            "gex_contrib": "gex_contrib_curr",
        }
    )
    merged = c.merge(p, on=_KEYS, how="left")

    for col in ("oi_prev", "gex_contrib_prev"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
    for col in ("oi_curr", "oi_change_vendor", "volume", "gex_contrib_curr"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged["d_oi"] = merged["oi_curr"].fillna(0.0) - merged["oi_prev"]
    merged["d_gex_contrib"] = merged["gex_contrib_curr"].fillna(0.0) - merged["gex_contrib_prev"]
    vol = merged["volume"].where(merged["volume"].fillna(0.0) > 0, np.nan)
    merged["conversion"] = (merged["d_oi"].abs() / vol).replace([np.inf, -np.inf], np.nan)

    return merged[_FRAME_COLS].sort_values(["expiry", "strike", "cp"]).reset_index(drop=True)


def load_oi_change_frame(session: Session, symbol: str) -> pd.DataFrame | None:
    """The day-over-day per-strike frame for ``symbol``, or ``None``.

    ``None`` when fewer than two EOD snapshots are stored.
    """
    snaps = load_recent_eod(session, symbol, n=2)
    if len(snaps) < 2:
        return None
    (_, curr), (_, prev) = snaps[0], snaps[1]
    return build_oi_change_frame(prev, curr)


@dataclass(frozen=True)
class OiFlowSummary:
    """Descriptive day-over-day roll-up (regime read-through, not a signal)."""

    total_d_gex: float
    call_d_oi: float
    put_d_oi: float
    n_strikes: int
    note: str


def summarize_oi_change(frame: pd.DataFrame) -> OiFlowSummary:
    """Aggregate ΔGEX and call-vs-put ΔOI into a descriptive read-through."""
    if frame is None or frame.empty:
        return OiFlowSummary(0.0, 0.0, 0.0, 0, "No overlapping strikes.")
    total_d_gex = float(frame["d_gex_contrib"].sum())
    call_d_oi = float(frame.loc[frame["cp"] == "C", "d_oi"].sum())
    put_d_oi = float(frame.loc[frame["cp"] == "P", "d_oi"].sum())
    gex_dir = "more positive (longer-gamma)" if total_d_gex >= 0 else "more negative (shorter)"
    oi_dir = "calls" if call_d_oi >= put_d_oi else "puts"
    note = (
        f"Net GEX shifted {gex_dir} vs the prior session; OI was added more on "
        f"{oi_dir}. Descriptive only — not a trade signal."
    )
    return OiFlowSummary(
        total_d_gex=total_d_gex,
        call_d_oi=call_d_oi,
        put_d_oi=put_d_oi,
        n_strikes=len(frame),
        note=note,
    )


def top_oi_changes(frame: pd.DataFrame, *, by: str = "d_oi", n: int = 15) -> pd.DataFrame:
    """Strikes with the largest absolute change, ranked by ``by`` (d_oi/d_gex_contrib)."""
    if frame is None or frame.empty or by not in frame.columns:
        return pd.DataFrame(columns=_FRAME_COLS)
    ranked = frame.assign(_abs=frame[by].abs()).sort_values("_abs", ascending=False)
    return ranked.drop(columns="_abs").head(n).reset_index(drop=True)
