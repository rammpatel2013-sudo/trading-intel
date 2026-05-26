"""Pure data-prep for the Vol-Richness dashboard page.

Reads the rows the EOD ``vol_richness`` job wrote (latest scan day) and shapes a
sortable rich/cheap sheet per horizon. The heavy lifting (VRP, percentile, IV
rank, term/skew, the regime gate) all happened in the job; this layer only loads,
filters by horizon, and orders richest-first (cold rows — no standardization
history yet — sink to the bottom).

Side-effect-free and unit-testable on in-memory SQLite (create only the
``vol_richness`` table). Descriptive regime view only — FlashAlpha rule 4, no
signals. The standing tail-risk caption is mandatory context: a rich read is a
premium-sell *candidate*, never a recommendation, and short-vol is gated off in a
VIX stress regime by the job.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import VolRichness

#: Columns surfaced on the sheet, in display order. ``richness_score`` IS the VRP
#: percentile, so the raw ``vrp_pctile`` is dropped to avoid a duplicate column.
DISPLAY_COLS = [
    "symbol", "label", "richness_score", "vrp_pts", "iv_rank",
    "iv_atm", "fcst_rv", "term_slope", "skew_25d", "regime_zone",
]

#: Decimal vol columns shown as vol points (x100): 0.0607 -> 6.07.
PCT_POINT_COLS = ("iv_atm", "fcst_rv", "vrp_pts", "term_slope", "skew_25d")
#: 0..1 standardized columns shown on a 0..100 scale.
RANK_PCT_COLS = ("richness_score", "iv_rank")

_ALL_COLS = ["ts", "horizon_dte", *[c for c in DISPLAY_COLS if c != "symbol"], "symbol"]

#: Always shown beneath the sheet — the non-optional short-vol tail-risk caveat.
TAIL_RISK_NOTE = (
    "Descriptive regime read (FlashAlpha rule 4) — not a signal. 'Rich' = a "
    "premium-sell *candidate* to study (delta-hedged), never a recommendation; "
    "short-vol is gated OFF by the job in a VIX stress regime. Percentile/IV-rank "
    "read 'cold' until a name accrues ~20 sessions of history."
)

_ZONE_CAPTION = {
    "low": "VIX carry regime (< 22) — vol-selling environment; short-vol reads active.",
    "mid": "VIX fragility band (22-32) — transitional; short-vol allowed with caution.",
    "high": "VIX stress regime (> 32) — short-vol (rich) candidates GATED OFF (tail-risk overlay).",
}


def load_latest(session: Session) -> pd.DataFrame:
    """Load every ``vol_richness`` row from the most recent scan day.

    Returns an empty (typed) frame when nothing has been written yet.
    """
    latest_ts = session.execute(select(func.max(VolRichness.ts))).scalar_one_or_none()
    if latest_ts is None:
        return pd.DataFrame(columns=_ALL_COLS)
    rows = session.execute(
        select(VolRichness).where(VolRichness.ts == latest_ts)
    ).scalars().all()
    if not rows:
        return pd.DataFrame(columns=_ALL_COLS)
    return pd.DataFrame(
        [
            {
                "ts": r.ts, "symbol": r.symbol, "horizon_dte": r.horizon_dte,
                "iv_atm": r.iv_atm, "fcst_rv": r.fcst_rv, "vrp_pts": r.vrp_pts,
                "vrp_pctile": r.vrp_pctile, "iv_rank": r.iv_rank,
                "term_slope": r.term_slope, "skew_25d": r.skew_25d,
                "regime_zone": r.regime_zone, "richness_score": r.richness_score,
                "label": r.label,
            }
            for r in rows
        ]
    )


def richness_sheet(frame: pd.DataFrame, *, horizon: int) -> pd.DataFrame:
    """Filter to ``horizon`` and order richest-first (cold rows last).

    Sorted by ``richness_score`` descending, ties broken by raw ``vrp_pts``; rows
    with no score (cold start) fall to the bottom via ``na_position='last'``.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=DISPLAY_COLS)
    sub = frame[frame["horizon_dte"] == horizon].copy()
    if sub.empty:
        return pd.DataFrame(columns=DISPLAY_COLS)
    sub = sub.sort_values(
        by=["richness_score", "vrp_pts"], ascending=[False, False], na_position="last"
    ).reset_index(drop=True)
    return sub[DISPLAY_COLS]


def scale_for_display(sheet: pd.DataFrame) -> pd.DataFrame:
    """Scale a richness sheet into reader-friendly units (numeric, still sortable).

    Decimal vol columns become vol points (x100, e.g. 0.0607 -> 6.07); the 0..1
    standardized columns become 0..100. ``None``/missing values become ``NaN``.
    Non-numeric columns (symbol/label/regime_zone) pass through unchanged.
    """
    if sheet is None or sheet.empty:
        return sheet.copy() if sheet is not None else pd.DataFrame(columns=DISPLAY_COLS)
    out = sheet.copy()
    for col in (*PCT_POINT_COLS, *RANK_PCT_COLS):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * 100.0
    return out


def available_horizons(frame: pd.DataFrame) -> list[int]:
    """Sorted distinct horizons present in the loaded data (e.g. [30, 60])."""
    if frame is None or frame.empty or "horizon_dte" not in frame.columns:
        return []
    return sorted(int(h) for h in frame["horizon_dte"].dropna().unique())


def regime_caption(frame: pd.DataFrame) -> str:
    """Market regime caption from the (market-wide) ``regime_zone`` on the rows."""
    if frame is None or frame.empty or "regime_zone" not in frame.columns:
        return "VIX regime unavailable."
    zones = frame["regime_zone"].dropna()
    if zones.empty:
        return "VIX regime unavailable (no stored VIX level)."
    return _ZONE_CAPTION.get(str(zones.iloc[0]), "VIX regime unavailable.")
