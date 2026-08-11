"""Market-breadth reader — the latest ``breadth_snapshots`` row + short trends.

Pure DB read (no vendor calls): the ``scheduler.jobs.breadth`` job banks a row a
day; this assembles the newest one plus a few trailing sessions of A-D-line,
%-above-200-MA, and Bull/Bear-Line context for the synthesis engine / the AM
brief. Descriptor only (FlashAlpha rule 4).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import BreadthSnapshot

_DIV_DETAIL = {
    "confirming": "breadth confirms price (no gap)",
    "bearish_div": "price up, breadth lagging — top-warning gap",
    "bullish_div": "price down, breadth firmer — washout",
    "none": "no clear divergence",
    None: "building",
}


def build_breadth(session: Session, *, source: str = "fmp_sp500", trend: int = 30) -> dict[str, Any]:
    """Assemble the latest breadth snapshot + trailing trends into one dict."""
    rows = list(
        session.execute(
            select(BreadthSnapshot)
            .where(BreadthSnapshot.source == source)
            .order_by(BreadthSnapshot.ts.desc())
            .limit(trend)
        ).scalars()
    )
    if not rows:
        return {"found": False, "source": source}
    latest = rows[0]
    series = list(reversed(rows))  # oldest→newest for the trend arrays

    dist = None
    if latest.spx_close is not None and latest.bull_bear_line:
        dist = latest.spx_close / latest.bull_bear_line - 1.0

    return {
        "found": True,
        "as_of": latest.ts.isoformat() if latest.ts else None,
        "source": source,
        # regime (Norseman)
        "bull_bear_line": latest.bull_bear_line,
        "spx_close": latest.spx_close,
        "above_bbl": latest.above_bbl,
        "dist_to_bbl": dist,
        "bbl_trend": [r.bull_bear_line for r in series],
        "spx_trend": [r.spx_close for r in series],
        # breadth
        "advancers": latest.advancers,
        "decliners": latest.decliners,
        "net_adv": latest.net_adv,
        "ad_line": latest.ad_line,
        "ad_trend": [r.ad_line for r in series],
        "new_highs": latest.new_highs,
        "new_lows": latest.new_lows,
        "pct_above_50": latest.pct_above_50,
        "pct_above_200": latest.pct_above_200,
        "pct_above_200_trend": [r.pct_above_200 for r in series],
        "mcclellan_osc": latest.mcclellan_osc,
        "mcclellan_sum": latest.mcclellan_sum,
        "n_constituents": latest.n_constituents,
        # divergence (A-D line vs price)
        "divergence": {
            "state": latest.divergence_state,
            "length": latest.divergence_len,
            "detail": _DIV_DETAIL.get(latest.divergence_state, "—"),
        },
    }
