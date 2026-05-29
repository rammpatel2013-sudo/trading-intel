"""Price Cone — intraday forward cone over the live gamma/charm field, plus
the multi-day HAR-RV expected-range cone.

Two tabs:

1. **Intraday forward cone** — the live forward dealer gamma (or charm) field
   from now to the 16:00 ET close rendered as a strike × time gradient
   heatmap, with two bounding price paths (up / down) overlaid. The cone
   driver is selectable: ``vol`` (rigorous ±1σ from near-the-money ATM IV),
   ``gex`` (gamma-flip distance, sqrt-time faded), ``charm`` (directional
   drift implied by net charm), or ``vanna`` (move implied by re-hedging
   under a 1 IV-pt vol shift). Sources: ``forward_field_data`` +
   ``forward_cone_data``.
2. **Multi-day HAR-RV cone** — the basic forward lognormal envelope
   (±1σ / ±2σ) projected ``horizon_days`` ahead from spot using the symbol's
   HAR-RV vol forecast (EWMA fallback). Source: ``price_cone_data``.

Both views are regime descriptors (CLAUDE.md rule 4); the cone overlays are
expected-range scenarios, never directional signals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.forward_cone_data import (
    DRIVER_LABELS,
    DRIVERS,
    intraday_cone,
)
from trading_intel.dashboard.forward_field_data import build_forward_fields
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.live_gex_map_data import load_live_gex_day
from trading_intel.dashboard.price_cone_data import build_cone
from trading_intel.dashboard.symbols import gamma_page_symbols
from trading_intel.errors import TradingIntelError

# ── Visuals ────────────────────────────────────────────────────────────

_GOLD = "#f6c343"
_UP = "#2ecc71"
_DOWN = "#e74c3c"
_DARK = "plotly_dark"

# Granular RdBu gradient for the forward field heatmap. 21 stops -> smooth
# shading even with sparse data, matches the GEX-Surface palette.
_GRANULAR_RDBU = [
    [0.00, "#053061"], [0.05, "#1f4f86"], [0.10, "#2166ac"], [0.15, "#3784ba"],
    [0.20, "#4393c3"], [0.25, "#69a8d0"], [0.30, "#92c5de"], [0.35, "#b4d6e8"],
    [0.40, "#d1e5f0"], [0.45, "#e6eff5"], [0.50, "#f7f7f7"],
    [0.55, "#fce4e4"], [0.60, "#fddbc7"], [0.65, "#f8b8a4"], [0.70, "#f4a582"],
    [0.75, "#ec8366"], [0.80, "#d6604d"], [0.85, "#c14133"], [0.90, "#b2182b"],
    [0.95, "#8b0d20"], [1.00, "#67001f"],
]

# Yahoo symbol map for the 3-min live spot cache.
_YF_MAP = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX"}
_SPOT_TTL_SEC = 180


# ── Plumbing ───────────────────────────────────────────────────────────


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


@st.cache_data(ttl=_SPOT_TTL_SEC, show_spinner=False)
def _live_spot(symbol: str) -> float | None:
    """yfinance last_price, cached 3 min."""
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


# ── Intraday-cone figure (forward field + up/down) ─────────────────────


def _forward_field_figure(
    field: pd.DataFrame,
    cone: pd.DataFrame,
    anchor: float,
    greek_label: str,
    driver_label: str,
    symbol: str,
) -> go.Figure:
    """Forward gamma/charm field as a gradient heatmap with up/down cone overlay."""
    fig = go.Figure()

    if not field.empty:
        z = field.to_numpy()
        absmax = float(np.nanmax(np.abs(z))) if z.size else 1.0
        absmax = absmax if np.isfinite(absmax) and absmax > 0 else 1.0
        fig.add_trace(
            go.Heatmap(
                x=list(field.columns),
                y=[float(s) for s in field.index],
                z=z,
                colorscale=_GRANULAR_RDBU,
                zmid=0.0,
                zmin=-absmax,
                zmax=absmax,
                zsmooth="best",
                colorbar={"title": greek_label, "thickness": 12},
                hovertemplate="t=%{x}<br>strike=%{y}<br>%{z:.3g}<extra></extra>",
            )
        )

    # Anchor (spot) horizontal reference.
    fig.add_hline(
        y=anchor, line_color=_GOLD, line_dash="dot",
        annotation_text=f"spot {anchor:,.2f}", annotation_position="top left",
    )

    # Up/down cone paths.
    if not cone.empty:
        fig.add_trace(
            go.Scatter(
                x=cone["t"], y=cone["up"], name="up path",
                mode="lines", line={"color": _UP, "width": 2.5},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=cone["t"], y=cone["down"], name="down path",
                mode="lines", line={"color": _DOWN, "width": 2.5},
                fill="tonexty", fillcolor="rgba(246,195,67,0.10)",
            )
        )

    fig.update_layout(
        title=f"{symbol} — forward {greek_label.lower()} field + {driver_label}",
        template=_DARK, height=600,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Time (to 16:00 ET)")
    fig.update_yaxes(title_text="Strike / Price")
    return fig


# ── Multi-day HAR-RV cone figure (preserved from original) ─────────────


def _harrv_cone_figure(cone: pd.DataFrame, ann_vol: float, symbol: str) -> go.Figure:
    """Shaded ±1σ / ±2σ forward cone with the spot-anchor median line."""
    x = cone["day"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=cone["hi2"], line={"width": 0},
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=cone["lo2"], fill="tonexty",
                             fillcolor="rgba(246,195,67,0.10)", line={"width": 0},
                             name="2 SD", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=cone["hi1"], line={"width": 0},
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=cone["lo1"], fill="tonexty",
                             fillcolor="rgba(246,195,67,0.25)", line={"width": 0},
                             name="1 SD", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=x, y=cone["median"], name="spot anchor",
                             line={"color": _GOLD, "width": 2, "dash": "dot"}))
    fig.update_layout(
        title=f"{symbol} — forward price cone ({ann_vol:.0%} HAR-RV vol, zero drift)",
        template=_DARK, height=520,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Trading days ahead")
    fig.update_yaxes(title_text="Projected price")
    return fig


# ── Tabs ───────────────────────────────────────────────────────────────


def _tab_intraday(session: Session, symbol: str) -> None:
    live = _live_spot(symbol)

    driver = st.radio(
        "Cone driver", options=list(DRIVERS),
        format_func=lambda d: DRIVER_LABELS[d],
        index=0, horizontal=True,
    )
    field_greek = st.selectbox(
        "Forward field", options=["gamma", "charm"],
        format_func=lambda g: g.capitalize(),
        help="The gradient background — dealer ${greek} field projected to the close.",
    )
    scope_0dte = st.checkbox(
        "0DTE scope only", value=True,
        help="Filter the live chain to contracts expiring today (the true 0DTE strip).",
    )

    try:
        ts, anchor, grid, gamma_field, charm_field = build_forward_fields(
            session, symbol, spot=live, scope_0dte=scope_0dte,
        )
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not build forward field: {exc}")
        return

    if anchor is None or not grid or len(grid) < 2:
        st.info(
            f"No live chain or session has closed for {symbol}. The intraday "
            "cone runs during RTH (09:30-16:00 ET)."
        )
        return

    field = gamma_field if field_greek == "gamma" else charm_field
    if field.empty:
        st.info(f"No contracts in scope for the {field_greek} field.")

    # Build the cone over the same time grid as the field.
    frame = load_live_gex_day(session, symbol)
    cone = intraday_cone(driver, frame, anchor, list(grid))

    if ts is not None:
        st.caption(freshness_caption(pd.Timestamp(ts).to_pydatetime(), label="Snapshot"))
    if live is not None:
        st.caption(f"Anchor: **{anchor:,.2f}** (live, 3-min cache).")
    if cone.empty:
        st.warning(
            f"Cone driver '{driver}' could not be computed (missing IV / "
            "gamma-flip / net gamma — see playbook). Field still shown."
        )

    greek_label = "$Gamma per 1% move" if field_greek == "gamma" else "Charm × OI"
    fig = _forward_field_figure(
        field, cone, anchor, greek_label, DRIVER_LABELS[driver], symbol
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        f"Driver: **{DRIVER_LABELS[driver]}** · Field: **{greek_label}** · "
        f"Anchor: {anchor:,.2f}. Up/down paths are bounding scenarios from "
        "now to the close, NOT a directional call (FlashAlpha rule 4)."
    )


def _tab_multiday(session: Session, symbol: str) -> None:
    horizon = st.slider("Horizon (trading days)", 5, 63, 21, 1, key="harrv_horizon")
    live = _live_spot(symbol)
    try:
        ann_vol, anchor, cone = build_cone(
            session, symbol, spot=live, horizon_days=horizon,
        )
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not build the HAR-RV cone: {exc}")
        return

    if cone.empty or ann_vol is None or anchor is None:
        st.info(
            f"Not enough price history for {symbol} yet — the cone needs stored "
            "daily closes (quotes_daily) to forecast vol."
        )
        return

    anchor_label = "live quote" if live is not None else "last close"
    st.caption(
        f"Anchor: **{anchor:,.2f}** ({anchor_label}). "
        f"Forecast vol: **{ann_vol:.1%}** annualized."
    )
    st.plotly_chart(_harrv_cone_figure(cone, ann_vol, symbol), use_container_width=True)


# ── Page ───────────────────────────────────────────────────────────────


def main() -> None:
    st.set_page_config(page_title="Price Cone", page_icon="📐", layout="wide")
    settings = get_settings()

    st.title("📐 Price cone — forward expected range")
    st.caption(
        "Two views: the intraday forward gamma/charm field with an up/down "
        "cone overlay (selectable driver) and the multi-day HAR-RV envelope. "
        "Both are descriptors (rule 4) — expected ranges, not direction."
    )

    factory = _session_factory()
    try:
        with factory() as session:
            symbols = gamma_page_symbols(session, settings)
            symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
            if not symbol:
                st.warning("No symbols configured in the watchlist.")
                return

            st.sidebar.caption("Spot updates every 3 min from yfinance.")
            _auto_refresh(_SPOT_TTL_SEC)

            tab_intra, tab_multi = st.tabs(
                ["Intraday forward cone", "Multi-day HAR-RV cone"]
            )
            with tab_intra:
                _tab_intraday(session, symbol)
            with tab_multi:
                _tab_multiday(session, symbol)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load Price Cone: {exc}")
        return


if __name__ == "__main__":
    main()
