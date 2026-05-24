"""Pure data-prep for the VIX dashboard page.

Reads stored ``vix_data`` rows and shapes the term-structure dict the CBOE client
returns. The regime zones come from MEMORY (VEGA/VIX zones): ``< 22`` carry,
``22-32`` fragility, ``> 32`` stress (crisis ~ 38.3). Side-effect-free and
unit-testable against in-memory SQLite (create only ``vix_data``). Descriptive
regime view only — FlashAlpha rule 4, no signals.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import VixData

#: Zone thresholds on the VIX level.
ZONE_LOW_MAX = 22.0
ZONE_MID_MAX = 32.0
CRISIS_LEVEL = 38.3

#: Approx calendar DTE for each CBOE term-structure tenor (for the x-axis).
TERM_DTE = {"VIX9D": 9, "VIX": 30, "VIX3M": 91, "VIX6M": 182}

_HIST_COLS = [
    "date", "vix", "vvix", "vix_sd20", "vvix_sd20", "hy_oas", "ig_oas", "vega_zone",
    "vix9d", "vix3m", "vix6m", "vrp",
]


def classify_zone(vix: float | None) -> str | None:
    """Map a VIX level to its regime zone label, or ``None`` if unknown."""
    if vix is None or (isinstance(vix, float) and pd.isna(vix)):
        return None
    if vix < ZONE_LOW_MAX:
        return "low"
    if vix <= ZONE_MID_MAX:
        return "mid"
    return "high"


def zone_caption(vix: float | None) -> str:
    """Human-readable regime read-through for the current VIX level."""
    zone = classify_zone(vix)
    if zone is None:
        return "VIX level unavailable."
    if zone == "high":
        crisis = " — crisis territory" if vix and vix >= CRISIS_LEVEL else ""
        label = f"stress regime (> 32){crisis}"
    else:
        label = {
            "low": "carry regime (< 22) — vol selling environment",
            "mid": "fragility band (22-32) — transitional",
        }[zone]
    return f"VIX {vix:.1f}: {label}. Descriptive regime read — not a signal."


def load_vix_history(session: Session, *, days: int = 180) -> pd.DataFrame:
    """Recent ``vix_data`` rows as a tidy frame, oldest first. Empty if none."""
    rows = list(
        session.execute(
            select(VixData).order_by(VixData.date.desc()).limit(days)
        ).scalars()
    )
    if not rows:
        return pd.DataFrame(columns=_HIST_COLS)
    frame = pd.DataFrame(
        [
            {
                "date": r.date,
                "vix": r.vix,
                "vvix": r.vvix,
                "vix_sd20": r.vix_sd20,
                "vvix_sd20": r.vvix_sd20,
                "hy_oas": r.hy_oas,
                "ig_oas": r.ig_oas,
                "vega_zone": r.vega_zone,
                "vix9d": r.vix9d,
                "vix3m": r.vix3m,
                "vix6m": r.vix6m,
                "vrp": r.vrp,
            }
            for r in rows
        ]
    )
    return frame.sort_values("date").reset_index(drop=True)


def term_structure_frame(term: dict[str, float | None] | None) -> pd.DataFrame:
    """Shape a CBOE term-structure dict into ``[tenor, dte, level]``, tenor-ordered.

    Drops tenors with no level. Empty frame for ``None``/empty input.
    """
    if not term:
        return pd.DataFrame(columns=["tenor", "dte", "level"])
    rows = [
        {"tenor": tenor, "dte": TERM_DTE.get(tenor, 0), "level": float(level)}
        for tenor, level in term.items()
        if level is not None and not (isinstance(level, float) and pd.isna(level))
    ]
    if not rows:
        return pd.DataFrame(columns=["tenor", "dte", "level"])
    return pd.DataFrame(rows).sort_values("dte").reset_index(drop=True)


def term_structure_from_row(row: "pd.Series | dict | None") -> pd.DataFrame:
    """Build a [tenor, dte, level] frame from a stored ``vix_data`` row.

    ``row`` is any mapping with ``vix9d`` / ``vix`` / ``vix3m`` / ``vix6m`` keys
    (e.g. a row of ``load_vix_history``). Lets us draw the term structure from
    persisted data (so it has history) rather than only a live CBOE fetch.
    """
    def _get(key: str) -> float | None:
        try:
            val = row[key]
        except (KeyError, IndexError, TypeError):
            return None
        return None if val is None or (isinstance(val, float) and pd.isna(val)) else val

    if row is None:
        return term_structure_frame(None)
    term = {
        "VIX9D": _get("vix9d"),
        "VIX": _get("vix"),
        "VIX3M": _get("vix3m"),
        "VIX6M": _get("vix6m"),
    }
    return term_structure_frame(term)


def classify_term_structure(term: pd.DataFrame, *, flat_band: float = 0.5) -> str | None:
    """Label the term-structure shape from a ``[tenor, dte, level]`` frame.

    Compares the shortest available tenor to the longest: ``contango`` (upward,
    near < far = calm), ``backwardation`` (inverted, near > far = front-end
    stress), or ``flat`` when the spread is within ``flat_band`` vol points.
    Needs >= 2 tenors; ``None`` otherwise.
    """
    if term is None or term.empty or len(term) < 2:
        return None
    ordered = term.sort_values("dte")
    spread = float(ordered.iloc[-1]["level"]) - float(ordered.iloc[0]["level"])
    if abs(spread) < flat_band:
        return "flat"
    return "contango" if spread > 0 else "backwardation"


def _safe_ratio(num: float | None, den: float | None) -> float | None:
    """``num / den`` guarding None/NaN/zero-denominator."""
    if num is None or den is None or pd.isna(num) or pd.isna(den) or den == 0:
        return None
    return float(num) / float(den)


def vvix_vix_ratio(vvix: float | None, vix: float | None) -> float | None:
    """VVIX / VIX: vol-of-vol relative to vol (elevated => latent fragility)."""
    return _safe_ratio(vvix, vix)


def near_term_stress(vix9d: float | None, vix: float | None) -> float | None:
    """VIX9D / VIX: > 1 warns of front-end backwardation (acute near-term stress)."""
    return _safe_ratio(vix9d, vix)
