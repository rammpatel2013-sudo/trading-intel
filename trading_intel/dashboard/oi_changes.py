"""Day-over-day positioning analytics over the wide EOD chain (``oi_chain_eod``).

Diffs the two most recent end-of-day per-strike snapshots for a symbol to
surface, per strike: change in open interest (our own ΔOI = today - yesterday,
cross-checked against Convex's native ``oi_change``), and how much of the volume
that PRODUCED that ΔOI "stuck" as new OI (``conversion`` = |ΔOI| / volume — new
positioning vs day-trade churn). Timing note: OI settles T+1 (published next
morning), so a snapshot's ΔOI reflects the *prior* session's trading — we divide
by that session's volume (``volume_prev``), not today's, so the read is aligned.
``volume`` (today's, pending its own OI print tomorrow) is carried for reference.
Also each strike's net-signed GEX contribution
(calls +, puts -, the project convention) plus its day-over-day change. Rolls up
to total ΔGEX and call-vs-put ΔOI.

These are descriptive regime lenses to help read positioning — NOT signals
(FlashAlpha rule 4). Nothing here emits an alert.
"""

from __future__ import annotations

import math
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
    "volume_prev", "volume", "conversion",
    "iv_prev", "iv_curr", "d_iv",
    "gex_contrib_prev", "gex_contrib_curr", "d_gex_contrib",
    "positioning",
]


def classify_positioning(d_oi: float | None, d_iv: float | None) -> str:
    """Descriptive positioning character from ΔOI paired with ΔIV at a strike.

    ΔOI tells you positioning *changed* but not its direction; pairing it with
    the same strike's day-over-day IV change disambiguates opening vs closing:
    new buying tends to firm IV at that strike, closing/unwinding tends to ease
    it. This is a *descriptive heuristic, not a law* (IV moves for other reasons
    too, and a buy-to-close still adds buy pressure) and never a signal
    (FlashAlpha rule 4).
    """
    rising = d_oi is not None and d_oi > 0
    falling = d_oi is not None and d_oi < 0
    iv_known = d_iv is not None and not (isinstance(d_iv, float) and math.isnan(d_iv))
    if iv_known and rising:
        return "opening, demand-led (IV up)" if d_iv > 0 else "opening, supply-led (IV down)"
    if iv_known and falling:
        return "closing/unwind (IV down)" if d_iv < 0 else "closing into firmer IV"
    if rising:
        return "opening interest"
    if falling:
        return "closing/unwind"
    return "little change"


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
                "iv": r.iv,
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

    p = prev[[*_KEYS, "oi", "gex_contrib", "iv", "volume"]].rename(
        columns={
            "oi": "oi_prev", "gex_contrib": "gex_contrib_prev", "iv": "iv_prev",
            "volume": "volume_prev",
        }
    )
    c = curr[[*_KEYS, "oi", "oi_change", "volume", "gex_contrib", "iv"]].rename(
        columns={
            "oi": "oi_curr", "oi_change": "oi_change_vendor",
            "gex_contrib": "gex_contrib_curr", "iv": "iv_curr",
        }
    )
    merged = c.merge(p, on=_KEYS, how="left")

    for col in ("oi_prev", "gex_contrib_prev"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)
    # IV/volume_prev left NaN when unknown (a new strike has no prior session) so
    # ΔIV and conversion stay undefined rather than treated as a move from zero.
    for col in ("oi_curr", "oi_change_vendor", "volume", "volume_prev",
                "gex_contrib_curr", "iv_curr", "iv_prev"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    merged["d_oi"] = merged["oi_curr"].fillna(0.0) - merged["oi_prev"]
    merged["d_iv"] = merged["iv_curr"] - merged["iv_prev"]
    merged["d_gex_contrib"] = merged["gex_contrib_curr"].fillna(0.0) - merged["gex_contrib_prev"]
    # ΔOI settles T+1, so it reflects the PRIOR session's trading — divide by that
    # session's volume (volume_prev), not today's, to time the read correctly.
    vol_prev = merged["volume_prev"].where(merged["volume_prev"].fillna(0.0) > 0, np.nan)
    merged["conversion"] = (merged["d_oi"].abs() / vol_prev).replace([np.inf, -np.inf], np.nan)

    d_oi, d_iv = merged["d_oi"], merged["d_iv"]
    iv_known = d_iv.notna()
    rising, falling = d_oi > 0, d_oi < 0
    merged["positioning"] = np.select(
        [
            rising & iv_known & (d_iv > 0),
            rising & iv_known & (d_iv < 0),
            falling & iv_known & (d_iv < 0),
            falling & iv_known & (d_iv > 0),
            rising & ~iv_known,
            falling & ~iv_known,
        ],
        [
            "opening, demand-led (IV up)",
            "opening, supply-led (IV down)",
            "closing/unwind (IV down)",
            "closing into firmer IV",
            "opening interest",
            "closing/unwind",
        ],
        default="little change",
    )

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
    mean_d_iv: float = 0.0


def summarize_oi_change(frame: pd.DataFrame) -> OiFlowSummary:
    """Aggregate ΔGEX and call-vs-put ΔOI into a descriptive read-through."""
    if frame is None or frame.empty:
        return OiFlowSummary(0.0, 0.0, 0.0, 0, "No overlapping strikes.")
    total_d_gex = float(frame["d_gex_contrib"].sum())
    call_d_oi = float(frame.loc[frame["cp"] == "C", "d_oi"].sum())
    put_d_oi = float(frame.loc[frame["cp"] == "P", "d_oi"].sum())
    mean_d_iv_raw = frame["d_iv"].mean() if "d_iv" in frame.columns else np.nan
    mean_d_iv = 0.0 if pd.isna(mean_d_iv_raw) else float(mean_d_iv_raw)
    gex_dir = "more positive (longer-gamma)" if total_d_gex >= 0 else "more negative (shorter)"
    oi_dir = "calls" if call_d_oi >= put_d_oi else "puts"
    if mean_d_iv > 0:
        iv_note = "IV firmed across changed strikes (consistent with net new buying)"
    elif mean_d_iv < 0:
        iv_note = "IV eased across changed strikes (consistent with closing/unwinding)"
    else:
        iv_note = "IV little changed"
    note = (
        f"Net GEX shifted {gex_dir} vs the prior session; OI was added more on "
        f"{oi_dir}; {iv_note}. Descriptive only — not a trade signal."
    )
    return OiFlowSummary(
        total_d_gex=total_d_gex,
        call_d_oi=call_d_oi,
        put_d_oi=put_d_oi,
        n_strikes=len(frame),
        note=note,
        mean_d_iv=mean_d_iv,
    )


def top_oi_changes(
    frame: pd.DataFrame, *, by: str = "d_oi", n: int = 15, sort_by_strike: bool = False
) -> pd.DataFrame:
    """Strikes with the largest absolute change, ranked by ``by`` (d_oi/d_gex_contrib).

    Selection is always the top ``n`` by magnitude. With ``sort_by_strike`` the
    result is then re-ordered by strike ascending for display (so it reads low
    strike -> high, e.g. 6400P before 9000C, instead of magnitude order).
    """
    if frame is None or frame.empty or by not in frame.columns:
        return pd.DataFrame(columns=_FRAME_COLS)
    ranked = frame.assign(_abs=frame[by].abs()).sort_values("_abs", ascending=False)
    top = ranked.drop(columns="_abs").head(n)
    if sort_by_strike:
        top = top.sort_values("strike", ascending=True)
    return top.reset_index(drop=True)
