"""Watchlist overview page — one descriptive row per ticker.

A sortable table of regime descriptors across the whole watchlist: net GEX (and
direction / weekly change), call/put OI ratio, vol/OI turnover, ATM skew, the
call/put walls + distance to spot, and a small set of descriptive gamma-squeeze
ingredients (dealer gamma regime from the flip, gamma concentration near spot,
call-wall proximity).

Thin shell: all math lives in ``dashboard/watchlist_metrics.py``. Weekly-change
and wall-drift cells read ``n/a`` until enough daily history has accrued.

FlashAlpha rule (CLAUDE.md rule 4): descriptors only — this page does not
predict squeezes or "explosive moves". That waits on the probability model.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.watchlist_metrics import (
    DISPLAY_LABELS,
    format_display,
    load_watchlist_metrics,
)
from trading_intel.watchlist import effective_symbols


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def main() -> None:
    st.set_page_config(page_title="Watchlist", page_icon="🗂️", layout="wide")
    settings = get_settings()

    st.title("🗂️ Watchlist overview")
    st.caption(
        "Regime descriptors across the watchlist — net GEX, ratios, skew, walls, and "
        "descriptive gamma-squeeze read-throughs. NOT predictions or signals (FlashAlpha rule)."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            symbols = effective_symbols(session, settings)
            metrics = load_watchlist_metrics(session, symbols) if symbols else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load watchlist metrics: {exc}")
        return

    if not symbols:
        st.warning("No symbols configured in the watchlist.")
        return
    if metrics is None or metrics.empty:
        st.info("No data stored yet for any watchlist symbol.")
        return

    ordered = metrics[[c for c in DISPLAY_LABELS if c in metrics.columns]]
    st.dataframe(format_display(ordered), use_container_width=True, hide_index=True)

    st.caption(
        "ΔGEX (1wk) and skew/wall drift fill in as daily history accrues (live from "
        "2026-05-22). Gamma regime: spot below the flip = dealers short gamma "
        "(move-amplifying); above = long gamma (move-damping). gamma-conc ±3% = share of "
        "gamma-OI within ±3% of spot."
    )

    with st.expander("What these columns mean"):
        st.markdown(
            "- **Net GEX / GEX dir / ΔGEX (1wk)** — net signed gamma-OI now, its "
            "day-over-day direction, and the change vs ~a week ago.\n"
            "- **Gamma regime / GEX flip** — dealer positioning regime implied by spot "
            "vs the zero-gamma price.\n"
            "- **C/P OI** — total call OI / put OI. **Vol/OI** — traded volume / OI "
            "(turnover; elevated values flag fresh positioning).\n"
            "- **Skew** — nearest-expiry OTM-put IV minus OTM-call IV (positive = puts "
            "richer, the usual index skew).\n"
            "- **Call/Put wall, CW dist** — strikes carrying the most call/put gamma-OI "
            "and how far the call wall sits above/below spot.\n"
            "- **gamma-conc ±3%** — gamma-OI concentration near spot (a tighter pin zone). "
            "Descriptive only — not a squeeze prediction."
        )


main()
