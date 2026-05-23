"""GEX-by-strike time-series page (Convex-style gxoi-by-strike heatmap).

For SPX/SPY/QQQ (or any effective-watchlist symbol), draws net signed GEX per
strike (calls +, puts -) as a strike x time heatmap on a diverging,
zero-centered scale (red short-gamma / blue long-gamma), with spot and the
gamma-flip price overlaid as lines, plus a companion bar of the latest
snapshot's GEX-by-strike profile.

Daily resolution: ``chain_snapshot`` runs once daily, so one column per trading
day; the view fills in as sessions accumulate. Thin shell — all data prep lives
in ``dashboard/gex_surface.py`` (pure, unit-tested). Per the FlashAlpha rule
(CLAUDE.md rule 4) every panel here is a *regime descriptor*, not a signal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.gex_surface import (
    gex_strike_matrix,
    load_gex_strike_series,
    spot_flip_overlay,
)
from trading_intel.errors import TradingIntelError

_POS = "#2ecc71"
_GOLD = "#f6c343"
_PREFERRED = ("SPX", "SPY", "QQQ")


def _session_factory() -> sessionmaker[Session]:
    """Reuse the factory the Home composition root injected, else build one."""
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _ordered_symbols(symbols: list[str]) -> list[str]:
    """Float SPX/SPY/QQQ to the front of the selector, keep the rest in order."""
    preferred = [s for s in _PREFERRED if s in symbols]
    rest = [s for s in symbols if s not in _PREFERRED]
    return preferred + rest


def _surface_figure(
    matrix: pd.DataFrame, overlay: pd.DataFrame, symbol: str
) -> go.Figure:
    """Diverging zero-centered GEX heatmap with spot + flip overlay lines."""
    absmax = float(np.nanmax(np.abs(matrix.to_numpy()))) if not matrix.empty else 1.0
    absmax = absmax if np.isfinite(absmax) and absmax > 0 else 1.0
    fig = go.Figure(
        go.Heatmap(
            x=list(matrix.columns),
            y=list(matrix.index),
            z=matrix.to_numpy(),
            colorscale="RdBu",
            zmid=0.0,
            zmin=-absmax,
            zmax=absmax,
            colorbar={"title": "Net GEX"},
            hovertemplate="ts=%{x}<br>strike=%{y}<br>net GEX=%{z:.3g}<extra></extra>",
        )
    )
    if not overlay.empty:
        if overlay["spot"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=overlay["ts"], y=overlay["spot"], name="spot",
                    mode="lines+markers", line={"color": _GOLD, "width": 2},
                )
            )
        if overlay["gex_flip"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=overlay["ts"], y=overlay["gex_flip"], name="gamma flip",
                    mode="lines+markers", line={"color": _POS, "width": 2, "dash": "dot"},
                )
            )
    fig.update_layout(
        title=f"{symbol} — net GEX by strike over time (calls +, puts -)",
        template="plotly_dark", height=540,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Snapshot")
    fig.update_yaxes(title_text="Strike")
    return fig


def _latest_profile_figure(series: pd.DataFrame, symbol: str) -> go.Figure | None:
    """Companion bar: net GEX by strike for the most recent snapshot."""
    if series.empty:
        return None
    latest_ts = series["ts"].max()
    latest = series[series["ts"] == latest_ts].sort_values("strike")
    colors = ["#5dade2" if v >= 0 else "#e74c3c" for v in latest["net_gex"]]
    fig = go.Figure(
        go.Bar(x=latest["strike"], y=latest["net_gex"], marker_color=colors, name="net GEX")
    )
    fig.update_layout(
        title=f"{symbol} — latest profile ({pd.Timestamp(latest_ts).date()})",
        template="plotly_dark", height=320,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, showlegend=False,
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text="Net GEX")
    return fig


def main() -> None:
    st.set_page_config(page_title="GEX Surface", page_icon="🗺️", layout="wide")
    settings = get_settings()
    symbols = _ordered_symbols(list(settings.watchlist_symbols))

    st.title("🗺️ GEX surface — strike x time")
    st.caption(
        "Net signed GEX (calls +, puts -) per strike over time. Regime descriptor "
        "only — not a trade signal (FlashAlpha rule). Daily resolution: one column "
        "per trading day as chain snapshots accumulate."
    )

    symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
    days = st.sidebar.slider("Lookback (days)", min_value=5, max_value=120, value=30, step=5)
    pct = st.sidebar.slider(
        "Strike range (± % of spot)", min_value=1.0, max_value=15.0, value=3.0, step=0.5
    )
    full_chain = st.sidebar.checkbox("Show full chain (ignore range)", value=False)
    pct_range = None if full_chain else pct / 100.0
    near_only = st.sidebar.checkbox("Near-term expiries only", value=False)
    expiry_within = (
        st.sidebar.slider("Expiry within (DTE)", min_value=1, max_value=90, value=7, step=1)
        if near_only
        else None
    )
    if not symbol:
        st.warning("No symbols configured in the watchlist.")
        return

    try:
        factory = _session_factory()
        with factory() as session:
            series = load_gex_strike_series(
                session, symbol, days=days,
                expiry_within_days=expiry_within, pct_range=pct_range,
            )
            overlay = spot_flip_overlay(session, symbol, days=days)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load GEX surface for {symbol}: {exc}")
        return

    if series.empty:
        st.info(
            f"No chain snapshots stored for {symbol} yet. The surface fills in once "
            "the chain_snapshot collector has run at least once."
        )
        return

    n_snaps = series["ts"].nunique()
    matrix = gex_strike_matrix(series)
    st.plotly_chart(_surface_figure(matrix, overlay, symbol), use_container_width=True)
    if n_snaps < 2:
        st.caption(
            f"Only {n_snaps} snapshot so far — the time axis becomes meaningful after "
            "a few daily sessions accumulate."
        )

    profile = _latest_profile_figure(series, symbol)
    if profile is not None:
        st.plotly_chart(profile, use_container_width=True)


if __name__ == "__main__":
    main()
