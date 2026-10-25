"""SqueezeMetrics DIX/GEX: pure parsing + percentile helpers (no I/O).

The free public file ``https://squeezemetrics.com/monitor/static/DIX.csv`` has
four columns ``date,price,dix,gex`` and runs from 2011 to the prior session
(they post after the close; some days land the next morning). This module
turns the CSV text into row dicts and ranks a value against its own history.

Usage rule (build plan, 2026-10-24): read THEIR gex by **percentile, not sign**.
Their model and our OI-based book agree on regime (level corr 0.66) but not day
to day (daily-change corr 0.36), and their sign can differ from ours.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

_HEADER = ("date", "price", "dix", "gex")


def parse_dix_csv(text: str) -> list[dict]:
    """Parse the DIX.csv body into ``[{date, spx_price, dix, gex}, ...]``.

    Tolerant of a header row, blank lines, and bad numeric cells (skipped).
    Dates must be ISO ``YYYY-MM-DD``. Output is sorted by date, de-duplicated
    on date (last one wins).
    """
    rows: dict[date, dict] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4 or parts[0].lower() == _HEADER[0]:
            continue
        try:
            d = datetime.strptime(parts[0], "%Y-%m-%d").date()
        except ValueError:
            continue
        rows[d] = {
            "date": d,
            "spx_price": _to_float(parts[1]),
            "dix": _to_float(parts[2]),
            "gex": _to_float(parts[3]),
        }
    return [rows[k] for k in sorted(rows)]


def _to_float(s: str) -> float | None:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN guard


def percentile_rank(history: Iterable[float | None], value: float | None) -> float | None:
    """Percentile (0..100) of ``value`` within ``history`` (``None``s dropped).

    Share of historical values strictly below ``value`` plus half of the ties,
    so a value equal to every historical point ranks 50. ``None`` when either
    side is missing or the history is empty.
    """
    if value is None:
        return None
    hist = [h for h in history if h is not None]
    if not hist:
        return None
    below = sum(1 for h in hist if h < value)
    ties = sum(1 for h in hist if h == value)
    return 100.0 * (below + 0.5 * ties) / len(hist)


def gex_regime_label(pctile: float | None) -> str:
    """Plain-English lane for the fear-release strength label.

    ≤30th percentile of the trailing year = "low gamma" (the label the
    backtest used: 2/2 strength = +3.39% 20d vs +1.11% base). ≥70th = "high".
    """
    if pctile is None:
        return "n/a"
    if pctile <= 30.0:
        return "low"
    if pctile >= 70.0:
        return "high"
    return "mid"
