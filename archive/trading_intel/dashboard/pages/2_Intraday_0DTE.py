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
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.ticker_data import (
    intraday_by_strike,
    latest_snapshot,
    load_intraday_flow_series,
    load_latest_intraday_flow,
    volume_by_strike_side,
)

_POS = "#2ecc71"
_NEG = "#e74c3c"
_ACCENT = "#e84393"
_GREEKS = (("gamma", "#5dade2"), ("vanna", "#bb8fce"), ("charm", "#f6c343"))


def _fmt_mb(value: float | None) -> str:
    """Format a large exposure as K / M / B (e.g. 1.23B, -45.6M)."""
    if value is None or value != value:  # None or NaN
        return "n/a"
    a = abs(value)
    for unit, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if a >= scale:
            return f"{value / scale:,.2f}{unit}"
    return f"{value:,.0f}"


def _greek_md(label: str, value: float | None) -> str:
    """Coloured Streamlit markdown chip for a net greek exposure (green +, red -)."""
    if value is None or value != value:
        return f"**{label}**: n/a"
    color = "green" if value >= 0 else "red"
    return f"**{label}**: :{color}[{_fmt_mb(value)}]"


def _hedge_read(net_gamma: float | None, net_vanna: float | None, net_charm: float | None) -> str:
    """Descriptive read of how today's 0DTE positioning shapes dealer hedging.

    Pure read-through of the current signs (FlashAlpha rule 4) — describes the
    mechanical hedging tendency, NOT a prediction of where price goes.
    """
    parts: list[str] = []
    if net_gamma is not None and net_gamma == net_gamma:
        if net_gamma >= 0:
            parts.append(
                "Net 0DTE gamma is **positive** — dealers are long gamma, hedging "
                "*against* moves (sell rallies / buy dips), which tends to damp "
                "intraday range and pin toward heavy strikes."
            )
        else:
            parts.append(
                "Net 0DTE gamma is **negative** — dealers are short gamma, hedging "
                "*with* moves (buy strength / sell weakness), which tends to amplify "
                "intraday range once spot leaves the flip."
            )
    if net_vanna is not None and net_vanna == net_vanna:
        parts.append(
            "Vanna exposure means an IV shift re-hedges delta: a vol pop "
            f"{'adds to' if net_vanna >= 0 else 'fades'} that flow as the session moves."
        )
    if net_charm is not None and net_charm == net_charm:
        parts.append(
            "Charm is the decay-driven re-hedge into the close — it builds toward 16:00 "
            "ET as 0DTE theta accelerates, reinforcing the gamma tendency above."
        )
    if not parts:
        return "No intraday exposure yet to read."
    return " ".join(parts) + " Descriptive regime read, not a forecast (rule 4)."


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


def _volume_split_bar(
    split: pd.DataFrame, *, title: str, spot: float | None = None
) -> go.Figure | None:
    """Diverging traded-volume bar: calls up (green), puts down (red), by strike."""
    if split is None or split.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=split["strike"], y=split["call"], marker_color=_POS, name="calls"))
    fig.add_trace(go.Bar(x=split["strike"], y=-split["put"], marker_color=_NEG, name="puts"))
    if spot is not None:
        fig.add_vline(
            x=spot, line_color=_ACCENT,
            annotation_text=f"spot {spot:g}", annotation_position="top",
        )
    fig.update_layout(
        title=title, template="plotly_dark", height=320, barmode="relative",
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, bargap=0.1,
        legend={"orientation": "h", "y": 1.06},
    )
    fig.update_xaxes(title_text="Strike")
    fig.update_yaxes(title_text="Traded volume (calls +, puts -)")
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

    st.caption(freshness_caption(ts, label="Last 5-min update"))
    cols = st.columns(4)
    cols[0].metric("Spot", f"{spot:g}" if spot is not None else "n/a")
    nets: dict[str, float | None] = {}
    for (greek, _), c in zip(_GREEKS, cols[1:], strict=False):
        col = f"{greek}_vol{suf}"
        total = float(by_strike[col].sum()) if col in by_strike.columns else None
        nets[greek] = total
        c.markdown(_greek_md(greek, total))

    st.info(_hedge_read(nets.get("gamma"), nets.get("vanna"), nets.get("charm")))

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
    split = volume_by_strike_side(latest, col=vol_col)
    volfig = _volume_split_bar(
        split, title=f"Traded volume by strike ({vol_col}) - calls vs puts", spot=spot,
    )
    if volfig is not None:
        volcol.plotly_chart(volfig, use_container_width=True)
    else:
        volcol.info("No traded-volume split available yet.")


main()
