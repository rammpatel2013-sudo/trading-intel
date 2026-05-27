"""MM gamma profile — $Gamma vs spot reference, per expiry + All (VS3D-style).

Spot-ladder simulation: for a grid of hypothetical spot levels, recompute every
near-the-money option's Black-Scholes gamma (sticky-strike) and sum the
sign-weighted dealer dollar-gamma, decomposed by expiration. The thick black
"All expiries" curve's zero-crossing is the gamma-flip level — below it dealers
are short gamma (move-amplifying), above long (dampening).

Recompute is sanctioned for this simulated view (ADR-002); Convex pre-computed
greeks stay the default for the snapshot maps. Thin shell over
``dashboard/gamma_profile_data.py``. Descriptive regime view, not a signal (rule 4).
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.gamma_profile_data import (
    ALL,
    ZERO_DTE,
    build_profile,
    load_latest_chain,
    snapshot_spot,
)
from trading_intel.dashboard.live_gex_data import live_gex_symbols

_GOLD = "#f6c343"
_ALL_LINE = "#f5f5f5"
_YF_MAP = {"SPX": "^GSPC", "NDX": "^NDX", "RUT": "^RUT", "VIX": "^VIX"}


def _live_spot(symbol: str) -> float | None:
    """Best-effort live spot via yfinance (index symbols mapped); None on failure."""
    try:
        import yfinance as yf

        px = getattr(yf.Ticker(_YF_MAP.get(symbol, symbol)).fast_info, "last_price", None)
        return float(px) if px else None
    except Exception:  # best-effort; falls back to the snapshot spot
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


def _profile_fig(profile, spot: float, scope: str, symbol: str) -> go.Figure:  # noqa: ANN001
    """$Gamma vs spot-ref: a line per expiry + thick 'all', with the current-spot rule."""
    fig = go.Figure()
    x = list(profile.index)
    for col in [c for c in profile.columns if c != "all"]:
        fig.add_trace(go.Scatter(x=x, y=profile[col], name=col, mode="lines",
                                 line={"width": 1.4}))
    fig.add_trace(go.Scatter(x=x, y=profile["all"], name="All expiries", mode="lines",
                             line={"color": _ALL_LINE, "width": 3}))
    fig.add_hline(y=0.0, line_color="#888", line_width=1)
    fig.add_vline(x=spot, line_color=_GOLD, line_dash="dot",
                  annotation_text=f"spot {spot:,.0f}", annotation_position="top")
    fig.update_layout(
        title=f"{symbol} — MM net $Gamma by spot reference ({scope})",
        template="plotly_dark", height=560,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_xaxes(title_text="Spot reference")
    fig.update_yaxes(title_text="$Gamma (per 1% move, calls + / puts -)")
    return fig


def main() -> None:
    st.set_page_config(page_title="MM Gamma Profile", page_icon="📈", layout="wide")
    settings = get_settings()

    st.title("📈 MM gamma profile — $Gamma by spot reference")
    st.caption(
        "Spot-ladder simulation (sticky-strike): dealer dollar-gamma recomputed across "
        "hypothetical spot levels, per expiry + All expiries. The All curve's zero-cross "
        "is the gamma flip. Simulated/what-if view (ADR-002) — descriptive, not a signal."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            options = live_gex_symbols(session) or list(settings.intraday_symbols)
            symbol = st.sidebar.selectbox("Symbol", options, index=0 if options else None)
            scope = st.sidebar.radio("Expiry scope", [ALL, ZERO_DTE], index=0)
            span_pct = st.sidebar.slider("Spot ladder (± % of spot)", 2.0, 15.0, 7.0, 0.5)
            refresh = st.sidebar.selectbox("Auto-refresh", ["Off", "60s", "5 min"], index=2)
            ts, frame = load_latest_chain(session, symbol) if symbol else (None, None)
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

    st.caption(freshness_caption(ts, label="Snapshot"))
    snap_spot = snapshot_spot(frame)
    live = _live_spot(symbol)
    spot = live if live is not None else snap_spot
    if live is not None:
        st.caption(f"Spot: live quote {live:,.2f}.")
    if spot is None:
        st.warning("No spot available to anchor the ladder.")
        return

    profile = build_profile(
        frame, spot, scope=scope, ref=ts.date(), span=span_pct / 100.0
    )
    if profile.empty:
        st.info(f"No contracts in scope ({scope}) for {symbol} in the latest snapshot.")
        return

    st.plotly_chart(_profile_fig(profile, spot, scope, symbol), use_container_width=True)


if __name__ == "__main__":
    main()
