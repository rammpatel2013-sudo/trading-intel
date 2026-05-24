"""Calendar seasonality context for the market-timing view.

A light, well-known seasonal overlay: the "Sell in May and go away" window
(May-Oct historically the weaker half for US large-caps vs Nov-Apr) plus the
weekday. Pure and descriptive - FlashAlpha rule 4: seasonality is a weak prior,
not a trade signal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: "Sell in May" weaker-half months (May 1 - Oct 31).
WEAK_MONTHS = frozenset({5, 6, 7, 8, 9, 10})


@dataclass(frozen=True)
class SeasonalContext:
    as_of: date
    half: str  # "weak" | "strong"
    half_label: str
    in_sell_in_may: bool
    weekday: str
    note: str


def seasonal_context(d: date) -> SeasonalContext:
    """Seasonal context for date ``d`` (descriptive prior only)."""
    weak = d.month in WEAK_MONTHS
    note = (
        "'Sell in May' window - seasonally weaker half historically; a mild "
        "headwind prior, not a signal."
        if weak
        else "Seasonally stronger Nov-Apr half historically; a mild tailwind "
        "prior, not a signal."
    )
    return SeasonalContext(
        as_of=d,
        half="weak" if weak else "strong",
        half_label="weak half (May-Oct)" if weak else "strong half (Nov-Apr)",
        in_sell_in_may=weak,
        weekday=d.strftime("%A"),
        note=note,
    )
