"""Vol-Richness dashboard page — IV rich/cheap vs a forward RV forecast.

Thin shell over ``dashboard/vol_richness_data.py`` (pure) + the ``vol_richness``
rows the EOD scanner writes. Shows a sortable rich/cheap sheet per horizon (30d
headline / 60d confirmation): the variance-risk premium (``vrp_pts`` = ATM IV -
HAR forward RV) standardized to each name's own history (``richness_score`` =
VRP percentile, plus IV rank), the term slope and 25Δ skew context, and the
VEGA/VIX regime-gated label.

All compute happened in the job — this page only loads, lets you pick the
horizon, and orders richest-first. Descriptive regime view, not a signal
(FlashAlpha rule 4); the standing tail-risk caption is mandatory context.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.styling import richness_color, zone_color
from trading_intel.dashboard.vol_richness_data import (
    TAIL_RISK_NOTE,
    available_horizons,
    load_latest,
    regime_caption,
    richness_sheet,
    scale_for_display,
)
from trading_intel.errors import TradingIntelError


def _style_sheet(df: pd.DataFrame) -> pd.io.formats.style.Styler:
    """Colour the Richness score and regime zone (descriptive only)."""
    return (
        df.style
        .map(lambda v: f"color: {richness_color(v)}", subset=["richness_score"])
        .map(lambda v: f"color: {zone_color(v)}", subset=["regime_zone"])
    )


def _num(label: str, fmt: str, help_text: str) -> st.column_config.NumberColumn:
    return st.column_config.NumberColumn(label, format=fmt, help=help_text)


def _column_config() -> dict:
    """Readable headers + numeric formats (vol points / 0-100 scores)."""
    return {
        "symbol": st.column_config.TextColumn("Symbol"),
        "label": st.column_config.TextColumn("Read", width="medium"),
        "richness_score": _num("Richness", "%.0f", "VRP percentile vs own history."),
        "vrp_pts": _num("VRP (vol pts)", "%.2f", "ATM IV minus forward RV, vol points."),
        "iv_rank": _num("IV rank", "%.0f", "Classic IV rank (0-100)."),
        "iv_atm": _num("ATM IV %", "%.1f", "ATM implied vol, annualized %."),
        "fcst_rv": _num("Fcst RV %", "%.1f", "HAR forward realized-vol forecast, %."),
        "term_slope": _num("Term slope (pts)", "%.2f", "60d minus 30d ATM IV, vol points."),
        "skew_25d": _num("25d skew (pts)", "%.2f", "25-delta put skew, vol points."),
        "regime_zone": st.column_config.TextColumn("Regime"),
    }


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def main() -> None:
    st.set_page_config(page_title="Vol Richness", page_icon="🌊", layout="wide")
    st.title("🌊 Vol Richness — IV rich/cheap vs forward RV")

    try:
        factory = _session_factory()
        with factory() as session:
            frame = load_latest(session)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load vol_richness data: {exc}")
        return

    if frame.empty:
        st.info(
            "No vol_richness data yet. The EOD scanner writes one row per "
            "(symbol, day, horizon) after the close (needs the day's oi_chain_eod "
            "chain + quotes_daily history) — this lights up after the first run."
        )
        return

    horizons = available_horizons(frame)
    as_of = frame["ts"].max()
    horizon = st.radio(
        "Horizon", horizons, index=0, horizontal=True, format_func=lambda h: f"{h}d"
    )

    st.caption(f"Scan date: **{as_of}** -- {regime_caption(frame)}")

    sheet = richness_sheet(frame, horizon=horizon)
    if sheet.empty:
        st.info(f"No rows for the {horizon}d horizon on the latest scan.")
        return

    st.dataframe(
        _style_sheet(scale_for_display(sheet)),
        use_container_width=True,
        hide_index=True,
        column_config=_column_config(),
    )
    st.caption(TAIL_RISK_NOTE)

    with st.expander("How to read this", expanded=False):
        st.markdown(
            "- **Richness**: VRP percentile vs this name's own forward-RV history "
            "(high = options rich, a premium-sell *candidate*; low = cheap, a "
            "long-vol candidate). 'cold' = not enough history yet.\n"
            "- **30d vs 60d**: 30d is the headline read; 60d (~VIX3M) confirms. A "
            "30↔60 divergence is itself a term-structure tell.\n"
            "- **regime gate**: in a VIX stress regime (> 32) the scanner gates "
            "short-vol (rich) reads OFF — selling vol into stress is the classic "
            "blow-up.\n"
            "- Descriptor only: this is decision-support for manual study, never an "
            "entry/exit signal (FlashAlpha rule 4)."
        )


if __name__ == "__main__":
    main()
