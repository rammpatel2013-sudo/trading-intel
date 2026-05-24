"""Charts - per-symbol price + RSI + IV-HV + GEX + DEX time series.

Stacked, shared-x panels over the stored history: price (quotes_daily), RSI
(Wilder, off close), IV-HV spread (greeks_snapshots ATM IV - quotes_daily rv20),
and GEX / DEX (greeks_snapshots). A charting view of how positioning + vol are
evolving. Descriptive - not signals (FlashAlpha rule 4).
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.chart_data import chart_frame, list_chart_symbols
from trading_intel.errors import TradingIntelError


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def main() -> None:
    st.set_page_config(page_title="Charts", page_icon="📉", layout="wide")
    st.title("📉 Charts — price + RSI + IV-HV + GEX + DEX")

    factory = _session_factory()
    try:
        with factory() as session:
            symbols = list_chart_symbols(session)
            if not symbols:
                st.info("No quotes_daily / greeks_snapshots data yet.")
                return
            default_i = symbols.index("SPX") if "SPX" in symbols else 0
            symbol = st.selectbox("Symbol", symbols, index=default_i)
            df = chart_frame(session, symbol)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load data: {exc}")
        return

    if df.empty:
        st.warning(f"No data for {symbol}.")
        return

    x = df["date"]
    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.30, 0.16, 0.18, 0.18, 0.18],
        subplot_titles=("Price", "RSI(14)", "IV-HV (vol pts)", "GEX", "DEX"),
    )
    if "close" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["close"], mode="lines", name="Close"), row=1, col=1)
    if "rsi" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["rsi"], mode="lines", name="RSI"), row=2, col=1)
        fig.add_hline(y=70, row=2, col=1, line_color="#e74c3c", line_dash="dot")
        fig.add_hline(y=30, row=2, col=1, line_color="#2ecc71", line_dash="dot")
    if "iv_hv" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["iv_hv"], mode="lines", name="IV-HV"), row=3, col=1)
        fig.add_hline(y=0, row=3, col=1, line_color="#888", line_dash="dot")
    if "gex" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["gex"], mode="lines", name="GEX"), row=4, col=1)
    if "dex" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["dex"], mode="lines", name="DEX"), row=5, col=1)
    fig.update_layout(
        template="plotly_dark", height=840, showlegend=False,
        margin={"l": 10, "r": 10, "t": 40, "b": 10},
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Price/RSI from quotes_daily; IV-HV = ATM IV − rv20 (greeks_snapshots vs "
        "quotes_daily); GEX/DEX from greeks_snapshots. Price history is deep; "
        "GEX/DEX/IV-HV fill in as the daily snapshots accumulate. Descriptive, "
        "not signals (FlashAlpha rule 4)."
    )


if __name__ == "__main__":
    main()
