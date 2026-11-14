"""Live gamma / charm / vanna strike x time map (Menthor-Q style).

Intraday heatmap of net dealer greek exposure by strike over the session (from
the 10-min ``live_gex`` collector, delta-band filtered), with the spot path
overlaid and the latest per-strike profile beside it. Pick the greek: gamma
(gxoi), charm (charm x OI) or vanna (vanna x OI) — all net-signed calls +, puts -.

Thin shell over ``dashboard/live_gex_map_data.py`` (pure). Descriptive regime
view, not a signal (FlashAlpha rule 4); near-the-money only (delta band).
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.live_gex_data import live_gex_symbols
from trading_intel.dashboard.live_gex_map_data import (
    composite_matrix,
    composite_profile,
    exposure_matrix,
    filter_expiry_scope,
    latest_profile,
    load_live_gex_day,
    spot_path,
)

_GREEK_LABELS = {
    "combined": "Combined (gamma+vanna+charm)",
    "gamma": "Gamma (GEX)",
    "charm": "Charm",
    "vanna": "Vanna",
}
_POS = "#2ecc71"
_NEG = "#e74c3c"
_GOLD = "#f6c343"

# Index symbols -> their yfinance quote ticker (for the live spot marker).
_YF_MAP = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX"}


def _live_spot(symbol: str) -> float | None:
    """Best-effort live spot via yfinance (index symbols mapped); None on failure."""
    try:
        import yfinance as yf

        px = getattr(yf.Ticker(_YF_MAP.get(symbol, symbol)).fast_info, "last_price", None)
        return float(px) if px else None
    except Exception:  # live quote is best-effort; falls back to the live_gex spot
        return None


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _auto_refresh(seconds: int) -> None:
    if seconds <= 0:
        return
    components.html(
        "<script>setTimeout(function(){window.parent.location.reload();}, "
        f"{seconds * 1000});</script>",
        height=0,
    )


def _heatmap_fig(matrix, spot_df, greek: str, symbol: str) -> go.Figure:  # noqa: ANN001
    """Strike x time diverging heatmap (red negative / green positive) + spot line."""
    z = matrix.to_numpy()
    absmax = float(np.nanmax(np.abs(z))) if z.size else 1.0
    absmax = absmax if np.isfinite(absmax) and absmax > 0 else 1.0
    fig = go.Figure(
        go.Heatmap(
            x=list(matrix.columns), y=[float(s) for s in matrix.index], z=z,
            colorscale="RdYlGn", zmid=0.0, zmin=-absmax, zmax=absmax,
            colorbar={"title": _GREEK_LABELS[greek]},
            hovertemplate="ts=%{x}<br>strike=%{y}<br>net=%{z:.3g}<extra></extra>",
        )
    )
    if not spot_df.empty:
        fig.add_trace(
            go.Scatter(x=spot_df["ts"], y=spot_df["spot"], name="spot",
                       mode="lines", line={"color": _GOLD, "width": 2})
        )
    fig.update_layout(
        title=f"{symbol} - net {_GREEK_LABELS[greek]} by strike over time",
        template="plotly_dark", height=540,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Time")
    fig.update_yaxes(title_text="Strike")
    return fig


def _profile_fig(profile, greek: str, spot: float | None) -> go.Figure:  # noqa: ANN001
    """Latest per-strike net exposure as horizontal bars (green +, red -)."""
    colors = [_POS if v >= 0 else _NEG for v in profile["exposure"]]
    fig = go.Figure(
        go.Bar(y=profile["strike"], x=profile["exposure"], orientation="h", marker_color=colors)
    )
    if spot is not None:
        fig.add_hline(y=spot, line_color=_GOLD, line_dash="dot",
                      annotation_text=f"spot {spot:g}", annotation_position="right")
    fig.update_layout(
        title=f"Latest {_GREEK_LABELS[greek]} profile", template="plotly_dark", height=540,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, showlegend=False,
    )
    fig.update_xaxes(title_text=f"net {_GREEK_LABELS[greek]}")
    fig.update_yaxes(title_text="Strike")
    return fig


def main() -> None:
    st.set_page_config(page_title="Live Gamma Map", page_icon="🟢", layout="wide")
    settings = get_settings()

    st.title("🟢 Live gamma / charm / vanna map")
    st.caption(
        "Intraday net dealer exposure by strike (calls +, puts -) from the 10-min "
        "live_gex collector. Near-the-money (delta band). Descriptive - not a signal."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            options = live_gex_symbols(session) or list(settings.intraday_symbols)
            symbol = st.sidebar.selectbox("Symbol", options, index=0 if options else None)
            greek = st.sidebar.radio("Greek", list(_GREEK_LABELS), index=0,
                                     format_func=lambda g: _GREEK_LABELS[g])
            scope = st.sidebar.radio("Expiry scope", ["All", "0DTE"], index=0)
            refresh = st.sidebar.selectbox("Auto-refresh", ["Off", "60s", "5 min"], index=2)
            frame = load_live_gex_day(session, symbol) if symbol else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load live GEX: {exc}")
        return

    _auto_refresh({"Off": 0, "60s": 60, "5 min": 300}[refresh])

    if not symbol:
        st.warning("No symbols with live GEX yet.")
        return
    if frame is None or frame.empty:
        st.info(
            "No live_gex data yet. The 10-min collector populates it during the "
            "regular session (09:30-16:00 ET) once it is running on the NAS."
        )
        return

    st.caption(freshness_caption(frame["ts"].max(), label="Last 10-min update"))
    frame = filter_expiry_scope(frame, scope)
    if frame.empty:
        st.info(f"No {scope} contracts in the latest snapshot for {symbol}.")
        return
    if scope == "0DTE":
        st.caption("Scoped to 0DTE — charm decay is exact for the expiring strip.")
    if greek == "combined":
        st.caption(
            "Composite = gamma + vanna + charm, each normalized to a common scale "
            "(calls +, puts -). Charm is weighted by the fraction of the 09:30-16:00 "
            "session remaining, so its pull fades to zero into the 16:00 close (0DTE)."
        )

    sp = spot_path(frame)
    live = _live_spot(symbol)
    if live is not None and not sp.empty:
        sp = sp.copy()
        sp.loc[sp.index[-1], "spot"] = live
        st.caption(f"Spot marker: live quote {live:,.2f}.")
    spot = live if live is not None else (float(sp["spot"].iloc[-1]) if not sp.empty else None)

    matrix = composite_matrix(frame) if greek == "combined" else exposure_matrix(frame, greek)
    profile = composite_profile(frame) if greek == "combined" else latest_profile(frame, greek)

    left, right = st.columns([2, 1])
    with left:
        st.plotly_chart(_heatmap_fig(matrix, sp, greek, symbol), use_container_width=True)
    with right:
        st.plotly_chart(_profile_fig(profile, greek, spot), use_container_width=True)


if __name__ == "__main__":
    main()
