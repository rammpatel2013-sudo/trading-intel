"""Intraday 0DTE/1DTE volume-flow page (SPX/SPY/QQQ).

Surfaces the 5-minute ``intraday_flow`` collector: how today's *traded volume*
in 0DTE/1DTE options is loading dealer gamma, vanna and charm — both as the
intraday build-up (time series) and as the current per-strike profile.

Two volume bases are available (per the collector): **cumulative** day volume
and the **interval** increment (fresh flow in the last 5-minute cycle).

Thin shell: all reads/aggregations live in ``dashboard/ticker_data.py``. Every
panel is a regime descriptor — no signals (FlashAlpha rule 4).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.ticker_data import (
    intraday_by_strike,
    latest_snapshot,
    load_intraday_flow_series,
    load_latest_intraday_flow,
)

_POS = "#2ecc71"
_NEG = "#e74c3c"
_ACCENT = "#e84393"
_GREEKS = (("gamma", "#5dade2"), ("vanna", "#bb8fce"), ("charm", "#f6c343"))


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _auto_refresh(seconds: int) -> None:
    """Reload the whole page every ``seconds`` (0 = off). Dependency-free."""
    if seconds <= 0:
        return
    ms = seconds * 1000
    components.html(
        "<script>setTimeout(function(){window.parent.location.reload();}, "
        f"{ms});</script>",
        height=0,
    )


def _suffix(basis: str) -> str:
    return "_iv" if basis == "interval (5-min)" else ""


def _series_panel(series: pd.DataFrame, basis: str) -> go.Figure | None:
    if series.empty:
        return None
    suf = _suffix(basis)
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True,
        subplot_titles=[f"{g} exposure (volume-weighted)" for g, _ in _GREEKS],
        vertical_spacing=0.08,
    )
    for i, (greek, color) in enumerate(_GREEKS, start=1):
        col = f"{greek}_vol{suf}"
        if col in series.columns:
            fig.add_trace(
                go.Scatter(
                    x=series["ts"], y=series[col], mode="lines+markers",
                    line={"color": color}, name=greek,
                ),
                row=i, col=1,
            )
    fig.update_layout(
        template="plotly_dark", height=560, showlegend=False,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
        title=f"Intraday build — {basis} volume",
    )
    return fig


def _strike_bar(
    by_strike: pd.DataFrame,
    col: str,
    *,
    title: str,
    spot: float | None = None,
    flip: float | None = None,
) -> go.Figure | None:
    if by_strike.empty or col not in by_strike.columns:
        return None
    colors = [_POS if v >= 0 else _NEG for v in by_strike[col]]
    fig = go.Figure(go.Bar(x=by_strike["strike"], y=by_strike[col], marker_color=colors))
    if spot is not None:
        fig.add_vline(
            x=spot, line_color=_ACCENT,
            annotation_text=f"spot {spot:g}", annotation_position="top",
        )
    if flip is not None:
        fig.add_vline(
            x=flip, line_color="#5dade2", line_dash="dot",
            annotation_text=f"flip {flip:g}", annotation_position="bottom",
        )
    fig.update_layout(
        title=title, template="plotly_dark", height=320,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, bargap=0.1,
    )
    fig.update_xaxes(title_text="Strike")
    return fig


def main() -> None:
    st.set_page_config(page_title="Intraday 0DTE", page_icon="⚡", layout="wide")
    settings = get_settings()
    symbols = settings.intraday_symbols

    st.title("⚡ Intraday 0DTE / 1DTE volume flow")
    st.caption(
        "Volume-weighted gamma/vanna/charm for 0DTE+1DTE at "
        f"±{settings.INTRADAY_STRIKE_RANGE:.0%} of spot. Regime descriptors only — "
        "not trade signals (FlashAlpha rule)."
    )

    symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
    basis = st.sidebar.radio("Volume basis", ["cumulative", "interval (5-min)"], index=0)
    refresh = st.sidebar.selectbox("Auto-refresh", ["Off", "30s", "60s", "5 min"], index=2)
    _auto_refresh({"Off": 0, "30s": 30, "60s": 60, "5 min": 300}[refresh])

    if not symbol:
        st.warning("No intraday symbols configured (INTRADAY_SYMBOLS).")
        return

    try:
        factory = _session_factory()
        with factory() as session:
            ts, latest = load_latest_intraday_flow(session, symbol)
            series = load_intraday_flow_series(session, symbol)
            snap = latest_snapshot(session, symbol)
    except SQLAlchemyError as exc:
        st.error(f"Could not load intraday flow for {symbol}: {exc}")
        return

    if ts is None:
        st.info(
            "No intraday 0DTE flow stored yet. The 5-minute collector populates this "
            "during the regular session (09:30-16:00 ET) once it is running on the NAS."
        )
        return

    by_strike = intraday_by_strike(latest)
    spot = float(latest["spot"].dropna().iloc[0]) if latest["spot"].notna().any() else None
    flip = snap.gex_flip if snap is not None else None
    suf = _suffix(basis)

    cols = st.columns(4)
    cols[0].metric("Spot", f"{spot:g}" if spot is not None else "n/a")
    cols[1].metric("Latest", ts.strftime("%H:%M") if ts is not None else "n/a")
    for (greek, _), c in zip(_GREEKS, cols[1:], strict=False):
        col = f"{greek}_vol{suf}"
        total = by_strike[col].sum() if col in by_strike.columns else None
        c.metric(f"{greek}_vol", f"{total:,.0f}" if total is not None else "n/a")

    series_fig = _series_panel(series, basis)
    if series_fig is not None:
        st.plotly_chart(series_fig, use_container_width=True)

    st.subheader(f"Per-strike profile @ {ts.strftime('%H:%M') if ts else ''} ({basis})")
    gcol, vcol = st.columns(2)
    gfig = _strike_bar(
        by_strike, f"gamma_vol{suf}", title="Gamma exposure by strike",
        spot=spot, flip=flip,
    )
    if gfig is not None:
        gcol.plotly_chart(gfig, use_container_width=True)
    vfig = _strike_bar(
        by_strike, f"vanna_vol{suf}", title="Vanna exposure by strike", spot=spot,
    )
    if vfig is not None:
        vcol.plotly_chart(vfig, use_container_width=True)

    ccol, volcol = st.columns(2)
    cfig = _strike_bar(
        by_strike, f"charm_vol{suf}", title="Charm exposure by strike", spot=spot,
    )
    if cfig is not None:
        ccol.plotly_chart(cfig, use_container_width=True)
    vol_col = "volume_interval" if suf == "_iv" else "volume"
    volfig = _strike_bar(
        by_strike, vol_col, title=f"Traded volume by strike ({vol_col})", spot=spot,
    )
    if volfig is not None:
        volcol.plotly_chart(volfig, use_container_width=True)


main()
