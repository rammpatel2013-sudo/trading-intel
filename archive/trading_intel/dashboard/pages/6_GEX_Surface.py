"""GEX Surface — three views over per-strike dealer positioning.

Tabs:

1. **Strike × time heatmap** — daily strike × time net-GEX heatmap (RdBu,
   zero-centered, granular shading) with spot + gamma-flip overlay, paired with
   a 4-profile detail panel for the latest snapshot: OI, GEX, Vanna, Delta by
   strike. Source: ``chain_snapshot`` (daily) + ``greeks_snapshots`` (overlay).
2. **Intraday levels** — today's 10-min spot path + near-term-expiry net GEX
   and Vanna by strike with an expiry multiselect. Source: ``live_gex`` (10-min)
   with a yfinance live spot updated every ~3 min.
3. **Daily levels** — the strike × time heatmap (price path + flip) **beside**
   all-expiry GEX and Vanna by-strike bars on a shared strike axis — the
   Menthor-Q price-and-levels view. So price and the level bars line up.

All three are regime descriptors only (CLAUDE.md rule 4). No signals here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.gex_surface import (
    aggregate_by_strike,
    gex_strike_matrix,
    load_gex_strike_series,
    load_latest_chain_rich,
    spot_flip_overlay,
)
from trading_intel.dashboard.live_gex_map_data import (
    exposure_matrix,
    filter_expiry_scope,
    latest_profile,
    load_live_gex_day,
    spot_path,
)
from trading_intel.errors import TradingIntelError

# ── Visuals ────────────────────────────────────────────────────────────

_GOLD = "#f6c343"
_GREEN = "#22c55e"          # vivid green for calls / positive
_GREEN_LIGHT = "#86efac"
_RED = "#ef4444"            # vivid red for puts / negative
_RED_LIGHT = "#fca5a5"
_BLUE = "#3b82f6"           # vivid blue for positive net (gex/vanna/dex)
_PINK = "#ec4899"           # vivid pink for negative net
_TEAL = "#06b6d4"           # neutral teal (unused; reserved)
_DARK = "plotly_dark"
_PREFERRED = ("SPX", "SPY", "QQQ")

# Custom diverging colorscale with extra interpolation points → granular shading
# on the heatmap. Plotly RdBu has 11 stops; this has 21 so the gradient renders
# smooth even with sparse data.
_GRANULAR_RDBU = [
    [0.00, "#053061"], [0.05, "#1f4f86"], [0.10, "#2166ac"], [0.15, "#3784ba"],
    [0.20, "#4393c3"], [0.25, "#69a8d0"], [0.30, "#92c5de"], [0.35, "#b4d6e8"],
    [0.40, "#d1e5f0"], [0.45, "#e6eff5"], [0.50, "#f7f7f7"],
    [0.55, "#fce4e4"], [0.60, "#fddbc7"], [0.65, "#f8b8a4"], [0.70, "#f4a582"],
    [0.75, "#ec8366"], [0.80, "#d6604d"], [0.85, "#c14133"], [0.90, "#b2182b"],
    [0.95, "#8b0d20"], [1.00, "#67001f"],
]

# Live spot refresh cadence — yfinance fast_info, cached for 3 min.
_SPOT_TTL_SEC = 180

# Index symbols -> yfinance ticker
_YF_MAP = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX"}


# ── Plumbing ───────────────────────────────────────────────────────────


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _ordered_symbols(symbols: list[str]) -> list[str]:
    preferred = [s for s in _PREFERRED if s in symbols]
    rest = [s for s in symbols if s not in _PREFERRED]
    return preferred + rest


@st.cache_data(ttl=_SPOT_TTL_SEC, show_spinner=False)
def _live_spot(symbol: str) -> float | None:
    """yfinance last_price for the index/etf; cached 3 min."""
    try:
        import yfinance as yf

        px = getattr(yf.Ticker(_YF_MAP.get(symbol, symbol)).fast_info, "last_price", None)
        return float(px) if px else None
    except Exception:
        return None


def _auto_refresh(seconds: int) -> None:
    if seconds <= 0:
        return
    components.html(
        "<script>setTimeout(function(){window.parent.location.reload();}, "
        f"{seconds * 1000});</script>",
        height=0,
    )


# ── Heatmap / overlay figure ───────────────────────────────────────────


def _heatmap_trace(matrix: pd.DataFrame) -> go.Heatmap:
    """Diverging zero-centered heatmap with smooth shading."""
    absmax = float(np.nanmax(np.abs(matrix.to_numpy()))) if not matrix.empty else 1.0
    absmax = absmax if np.isfinite(absmax) and absmax > 0 else 1.0
    return go.Heatmap(
        x=list(matrix.columns),
        y=list(matrix.index),
        z=matrix.to_numpy(),
        colorscale=_GRANULAR_RDBU,
        zmid=0.0,
        zmin=-absmax,
        zmax=absmax,
        zsmooth="best",  # bilinear smoothing → granular shading between cells
        colorbar={"title": "Net GEX", "thickness": 12},
        hovertemplate="ts=%{x}<br>strike=%{y}<br>net GEX=%{z:.3g}<extra></extra>",
    )


def _overlay_traces(overlay: pd.DataFrame) -> list[go.Scatter]:
    traces: list[go.Scatter] = []
    if overlay.empty:
        return traces
    if overlay["spot"].notna().any():
        traces.append(
            go.Scatter(
                x=overlay["ts"], y=overlay["spot"], name="spot",
                mode="lines+markers", line={"color": _GOLD, "width": 2.5},
            )
        )
    if overlay["gex_flip"].notna().any():
        traces.append(
            go.Scatter(
                x=overlay["ts"], y=overlay["gex_flip"], name="gamma flip",
                mode="lines+markers", line={"color": _GREEN, "width": 2, "dash": "dot"},
            )
        )
    return traces


def _heatmap_figure(matrix: pd.DataFrame, overlay: pd.DataFrame, symbol: str) -> go.Figure:
    fig = go.Figure(_heatmap_trace(matrix))
    for tr in _overlay_traces(overlay):
        fig.add_trace(tr)
    fig.update_layout(
        title=f"{symbol} — net GEX by strike over time",
        template=_DARK, height=540,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Snapshot")
    fig.update_yaxes(title_text="Strike")
    return fig


# ── 4-profile strip ────────────────────────────────────────────────────


def _oi_call_put_split(chain: pd.DataFrame) -> pd.DataFrame:
    """``[strike, call_oi, put_oi]`` — OI separated by side (both positive).

    Rendered as a back-to-back horizontal bar (puts on the left, calls on the
    right) so the distribution of open interest by side is immediately legible.
    """
    cols = ["strike", "call_oi", "put_oi"]
    if chain is None or chain.empty:
        return pd.DataFrame(columns=cols)
    df = chain.copy()
    oi = pd.to_numeric(df.get("oi", 0.0), errors="coerce").fillna(0.0)
    kind = df["opt_kind"].astype(str).str[0].str.upper()
    by_call = pd.DataFrame({"strike": df["strike"], "v": oi.where(kind == "C", 0.0)})
    by_put = pd.DataFrame({"strike": df["strike"], "v": oi.where(kind == "P", 0.0)})
    call_oi = by_call.groupby("strike", as_index=False)["v"].sum().rename(columns={"v": "call_oi"})
    put_oi = by_put.groupby("strike", as_index=False)["v"].sum().rename(columns={"v": "put_oi"})
    out = call_oi.merge(put_oi, on="strike", how="outer").fillna(0.0)
    return out[cols].sort_values("strike").reset_index(drop=True)


def _four_profile_strip(
    chain: pd.DataFrame, symbol: str, spot: float | None, ts: object
) -> go.Figure:
    """OI (call/put split) | Net GEX | Net Vanna×OI | Net DEX — by strike."""
    fig = make_subplots(
        rows=1, cols=4,
        shared_yaxes=True,
        horizontal_spacing=0.02,
        subplot_titles=[
            "OI (puts ◀ | ▶ calls)",
            "Net GEX",
            "Net Vanna×OI",
            "Net DEX (Δ×OI)",
        ],
    )

    # OI split — puts on the left (negative x), calls on the right (positive x).
    oi = _oi_call_put_split(chain)
    if not oi.empty:
        fig.add_trace(
            go.Bar(
                y=oi["strike"], x=-oi["put_oi"],
                orientation="h",
                marker_color=_RED, marker_line_width=0,
                name="put OI", showlegend=False,
                hovertemplate="strike=%{y}<br>put OI=%{customdata:.0f}<extra></extra>",
                customdata=oi["put_oi"],
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Bar(
                y=oi["strike"], x=oi["call_oi"],
                orientation="h",
                marker_color=_GREEN, marker_line_width=0,
                name="call OI", showlegend=False,
                hovertemplate="strike=%{y}<br>call OI=%{x:.0f}<extra></extra>",
            ),
            row=1, col=1,
        )

    # Signed metrics — vivid diverging palette.
    for col, kind in [(2, "gex"), (3, "vanna"), (4, "delta")]:
        agg = aggregate_by_strike(chain, kind)
        if agg.empty:
            continue
        colors = [_BLUE if v >= 0 else _PINK for v in agg["value"]]
        label = {"gex": "GEX", "vanna": "Vanna×OI", "delta": "DEX"}[kind]
        fig.add_trace(
            go.Bar(
                y=agg["strike"], x=agg["value"],
                orientation="h",
                marker_color=colors, marker_line_width=0,
                name=label, showlegend=False,
                hovertemplate=f"strike=%{{y}}<br>{label}=%{{x:.3g}}<extra></extra>",
            ),
            row=1, col=col,
        )

    # Spot reference line on every panel.
    if spot is not None:
        for col in (1, 2, 3, 4):
            fig.add_hline(y=spot, line_color=_GOLD, line_dash="dot", row=1, col=col)

    fig.update_layout(
        title=f"{symbol} — latest snapshot ({pd.Timestamp(ts).date()})",
        template=_DARK, height=560,
        margin={"l": 10, "r": 10, "t": 60, "b": 10}, showlegend=False,
        bargap=0.05, barmode="overlay",
    )
    fig.update_yaxes(title_text="Strike", row=1, col=1)
    # OI panel: symmetric x-axis around 0 so the split reads cleanly.
    if not oi.empty:
        absmax = float(max(oi["call_oi"].max(), oi["put_oi"].max()))
        if absmax > 0:
            fig.update_xaxes(range=[-absmax * 1.05, absmax * 1.05], row=1, col=1)
    return fig


# ── Menthor-Q view: heatmap + GEX/Vanna bars sharing strike axis ───────


def _menthor_q_figure(
    matrix: pd.DataFrame,
    overlay: pd.DataFrame,
    chain: pd.DataFrame,
    symbol: str,
    spot: float | None,
) -> go.Figure:
    """Heatmap (left) + GEX/Vanna by-strike bars (right) on a SHARED strike axis.

    So the spot path overlay on the heatmap lines up vertically with the level
    bars — what the user wanted.
    """
    fig = make_subplots(
        rows=1, cols=3,
        shared_yaxes=True,
        column_widths=[0.62, 0.19, 0.19],
        horizontal_spacing=0.015,
        subplot_titles=[f"{symbol} — net GEX strike × time", "Net GEX", "Net Vanna×OI"],
    )
    fig.add_trace(_heatmap_trace(matrix), row=1, col=1)
    for tr in _overlay_traces(overlay):
        fig.add_trace(tr, row=1, col=1)

    if not chain.empty:
        gex = aggregate_by_strike(chain, "gex")
        vanna = aggregate_by_strike(chain, "vanna")
        if not gex.empty:
            fig.add_trace(
                go.Bar(
                    y=gex["strike"], x=gex["value"],
                    orientation="h",
                    marker_color=[_BLUE if v >= 0 else _PINK for v in gex["value"]],
                    marker_line_width=0,
                    showlegend=False,
                    hovertemplate="strike=%{y}<br>GEX=%{x:.3g}<extra></extra>",
                ),
                row=1, col=2,
            )
        if not vanna.empty:
            fig.add_trace(
                go.Bar(
                    y=vanna["strike"], x=vanna["value"],
                    orientation="h",
                    marker_color=[_BLUE if v >= 0 else _PINK for v in vanna["value"]],
                    marker_line_width=0,
                    showlegend=False,
                    hovertemplate="strike=%{y}<br>Vanna×OI=%{x:.3g}<extra></extra>",
                ),
                row=1, col=3,
            )

    if spot is not None:
        for col in (1, 2, 3):
            fig.add_hline(y=spot, line_color=_GOLD, line_dash="dot", row=1, col=col)

    fig.update_layout(
        template=_DARK, height=620,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
        bargap=0.05,
    )
    fig.update_yaxes(title_text="Strike", row=1, col=1)
    fig.update_xaxes(title_text="Snapshot", row=1, col=1)
    return fig


# ── Intraday tab figure (spot path + bars) ─────────────────────────────


def _intraday_figure(
    frame: pd.DataFrame, symbol: str, live_spot_px: float | None
) -> go.Figure:
    """Today's spot path + near-term-expiry net GEX/Vanna by-strike bars."""
    sp = spot_path(frame)
    gex_prof = latest_profile(frame, "gamma")
    vanna_prof = latest_profile(frame, "vanna")

    fig = make_subplots(
        rows=1, cols=3,
        column_widths=[0.58, 0.21, 0.21],
        horizontal_spacing=0.02,
        subplot_titles=[f"{symbol} — 10-min spot path", "Net GEX (latest)", "Net Vanna×OI (latest)"],
    )

    if not sp.empty:
        fig.add_trace(
            go.Scatter(
                x=sp["ts"], y=sp["spot"], mode="lines+markers",
                line={"color": _GOLD, "width": 2.5}, name="spot",
                showlegend=False,
            ),
            row=1, col=1,
        )
    if live_spot_px is not None and not sp.empty:
        fig.add_trace(
            go.Scatter(
                x=[sp["ts"].max()], y=[live_spot_px], mode="markers",
                marker={"color": _GREEN, "size": 10, "symbol": "diamond"},
                name=f"live {live_spot_px:,.2f}", showlegend=False,
            ),
            row=1, col=1,
        )

    if not gex_prof.empty:
        fig.add_trace(
            go.Bar(
                y=gex_prof["strike"], x=gex_prof["exposure"],
                orientation="h",
                marker_color=[_BLUE if v >= 0 else _PINK for v in gex_prof["exposure"]],
                marker_line_width=0,
                showlegend=False,
                hovertemplate="strike=%{y}<br>GEX=%{x:.3g}<extra></extra>",
            ),
            row=1, col=2,
        )
    if not vanna_prof.empty:
        fig.add_trace(
            go.Bar(
                y=vanna_prof["strike"], x=vanna_prof["exposure"],
                orientation="h",
                marker_color=[_BLUE if v >= 0 else _PINK for v in vanna_prof["exposure"]],
                marker_line_width=0,
                showlegend=False,
                hovertemplate="strike=%{y}<br>Vanna×OI=%{x:.3g}<extra></extra>",
            ),
            row=1, col=3,
        )

    if live_spot_px is not None:
        for col in (1, 2, 3):
            fig.add_hline(y=live_spot_px, line_color=_GOLD, line_dash="dot", row=1, col=col)

    fig.update_layout(
        template=_DARK, height=560,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        bargap=0.05,
    )
    fig.update_yaxes(title_text="Spot / Strike", row=1, col=1)
    fig.update_yaxes(title_text="Strike", row=1, col=2)
    fig.update_xaxes(title_text="Time", row=1, col=1)
    return fig


# ── Sidebar ────────────────────────────────────────────────────────────


def _sidebar(symbols: list[str]) -> tuple[str, int, float | None, int | None]:
    symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
    days = st.sidebar.slider("Daily lookback (days)", min_value=5, max_value=120, value=30, step=5)
    pct = st.sidebar.slider(
        "Strike range (± % of spot)", min_value=1.0, max_value=15.0, value=3.0, step=0.5
    )
    full_chain = st.sidebar.checkbox("Show full chain (ignore range)", value=False)
    pct_range = None if full_chain else pct / 100.0
    near_only = st.sidebar.checkbox("Near-term expiries only", value=False)
    expiry_within = (
        st.sidebar.slider("Expiry within (DTE)", min_value=1, max_value=180, value=30, step=1)
        if near_only
        else None
    )
    st.sidebar.caption("Spot updates every 3 min from yfinance.")
    return symbol, days, pct_range, expiry_within


# ── Tabs ───────────────────────────────────────────────────────────────


def _tab_strike_time(
    session: Session, symbol: str, days: int, pct_range: float | None, expiry_within: int | None
) -> None:
    series = load_gex_strike_series(
        session, symbol, days=days, expiry_within_days=expiry_within, pct_range=pct_range,
    )
    overlay = spot_flip_overlay(session, symbol, days=days)
    if series.empty:
        st.info(f"No chain snapshots stored for {symbol} yet.")
        return

    st.caption(
        freshness_caption(pd.Timestamp(series["ts"].max()).to_pydatetime(), label="Latest snapshot")
    )

    live = _live_spot(symbol)
    if live is not None and not overlay.empty:
        overlay = overlay.copy()
        last = overlay.index[-1]
        overlay.loc[last, "spot"] = live

    matrix = gex_strike_matrix(series)
    st.plotly_chart(_heatmap_figure(matrix, overlay, symbol), use_container_width=True)

    ts_latest, chain = load_latest_chain_rich(session, symbol)
    if chain.empty:
        st.info("No rich chain available for the latest snapshot.")
        return
    marker = live if live is not None else (
        float(overlay["spot"].iloc[-1]) if not overlay.empty and pd.notna(overlay["spot"].iloc[-1]) else None
    )
    st.plotly_chart(_four_profile_strip(chain, symbol, marker, ts_latest), use_container_width=True)


def _tab_intraday(session: Session, symbol: str) -> None:
    frame = load_live_gex_day(session, symbol)
    if frame.empty:
        st.info(
            f"No `live_gex` rows for {symbol} today. The 10-min collector populates "
            "during RTH (09:30-16:00 ET) once it is running on the NAS."
        )
        return

    # Expiry multiselect (default: all stored today).
    if "expiry" in frame.columns:
        expiries = sorted(
            pd.to_datetime(frame["expiry"], errors="coerce").dropna().dt.date.unique().tolist()
        )
    else:
        expiries = []
    chosen = st.multiselect(
        "Expiries", options=expiries, default=expiries,
        help="Filter the intraday GEX/Vanna bars to a subset of expiries.",
    )
    if chosen and len(chosen) != len(expiries):
        exp = pd.to_datetime(frame["expiry"], errors="coerce").dt.date
        frame = frame[exp.isin(set(chosen))]

    st.caption(freshness_caption(pd.Timestamp(frame["ts"].max()).to_pydatetime(), label="Latest tick"))
    live = _live_spot(symbol)
    if live is not None:
        st.caption(f"Live spot (3-min refresh): **{live:,.2f}**")

    st.plotly_chart(_intraday_figure(frame, symbol, live), use_container_width=True)


def _tab_daily(
    session: Session, symbol: str, days: int, pct_range: float | None, expiry_within: int | None
) -> None:
    series = load_gex_strike_series(
        session, symbol, days=days, expiry_within_days=expiry_within, pct_range=pct_range,
    )
    overlay = spot_flip_overlay(session, symbol, days=days)
    if series.empty:
        st.info(f"No chain snapshots stored for {symbol} yet.")
        return

    live = _live_spot(symbol)
    if live is not None and not overlay.empty:
        overlay = overlay.copy()
        last = overlay.index[-1]
        overlay.loc[last, "spot"] = live

    matrix = gex_strike_matrix(series)
    ts_latest, chain = load_latest_chain_rich(session, symbol)
    marker = live if live is not None else (
        float(overlay["spot"].iloc[-1]) if not overlay.empty and pd.notna(overlay["spot"].iloc[-1]) else None
    )
    fig = _menthor_q_figure(matrix, overlay, chain, symbol, marker)
    if ts_latest is not None:
        st.caption(freshness_caption(pd.Timestamp(ts_latest).to_pydatetime(), label="Latest snapshot"))
    st.plotly_chart(fig, use_container_width=True)


# ── Page ───────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="GEX Surface", page_icon="🗺️", layout="wide")
    settings = get_settings()
    symbols = _ordered_symbols(list(settings.watchlist_symbols))

    st.title("🗺️ GEX surface — strike × time")
    st.caption(
        "Net signed GEX (calls +, puts -) per strike. Three views: the daily "
        "strike × time heatmap with a profile strip, the intraday live tier, "
        "and a Menthor-Q price-and-levels view. Descriptor only (FlashAlpha "
        "rule 4)."
    )

    if not symbols:
        st.warning("No symbols configured in the watchlist.")
        return

    symbol, days, pct_range, expiry_within = _sidebar(symbols)

    # Re-render every 3 min so the cached live spot refreshes.
    _auto_refresh(_SPOT_TTL_SEC)

    try:
        factory = _session_factory()
        with factory() as session:
            tab_st, tab_intra, tab_daily = st.tabs(
                ["Strike × time heatmap", "Intraday levels", "Daily levels"]
            )
            with tab_st:
                _tab_strike_time(session, symbol, days, pct_range, expiry_within)
            with tab_intra:
                _tab_intraday(session, symbol)
            with tab_daily:
                _tab_daily(session, symbol, days, pct_range, expiry_within)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load GEX surface for {symbol}: {exc}")
        return


if __name__ == "__main__":
    main()
