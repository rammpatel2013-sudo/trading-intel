"""Delta-notional flow page: price overlaid with cumulative call/put delta.

The intraday cumulative dollar-delta of the day's option flow (from the 5-minute
``delta_flow`` collector): price on the left axis, and four delta-notional lines
on the right axis -- calls vs puts, for ALL expiries and the NEXT expiry. Positive
= net call delta bought, negative = net put delta. Refreshes every few minutes.

Thin shell over ``dashboard/delta_flow_data.py`` (pure). Descriptive flow
read-through -- what actually traded, not a signal (FlashAlpha rule 4).
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.delta_flow_data import available_symbols, load_delta_flow_day
from trading_intel.dashboard.freshness import freshness_caption

# (column, legend label, colour) for the four delta-notional lines.
_LINES = (
    ("call_notional_all", "Calls (all)", "#e8842a"),
    ("put_notional_all", "Puts (all)", "#3b7be8"),
    ("call_notional_next", "Calls (next exp)", "#2ecc71"),
    ("put_notional_next", "Puts (next exp)", "#5dade2"),
)


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


def _flow_figure(df, symbol: str) -> go.Figure:  # noqa: ANN001 (pandas frame)
    """Price (left axis) overlaid with the four delta-notional lines (right axis)."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scatter(x=df["ts"], y=df["spot"], name="Price", line={"color": "#f5f5f5"}),
        secondary_y=False,
    )
    for col, label, color in _LINES:
        if col in df.columns:
            fig.add_trace(
                go.Scatter(x=df["ts"], y=df[col], name=label, line={"color": color}),
                secondary_y=True,
            )
    fig.update_layout(
        title=f"{symbol} - delta-notional flow (price + cumulative call/put delta)",
        template="plotly_dark", height=560,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
        legend={"orientation": "h", "y": 1.04},
    )
    fig.update_yaxes(title_text="Price", secondary_y=False)
    fig.update_yaxes(title_text="Delta notional ($)", tickformat="$~s",
                     secondary_y=True, showgrid=False)
    return fig


def main() -> None:
    st.set_page_config(page_title="Delta Flow", page_icon="🌊", layout="wide")
    settings = get_settings()

    st.title("🌊 Delta-notional flow")
    st.caption(
        "Cumulative dollar-delta of today's option flow: price + call/put delta "
        "notional (all expiries vs next expiry). Descriptive flow, not a signal "
        "(FlashAlpha rule)."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            stored = available_symbols(session)
            options = stored or list(settings.intraday_symbols)
            symbol = st.sidebar.selectbox("Symbol", options, index=0 if options else None)
            refresh = st.sidebar.selectbox("Auto-refresh", ["Off", "60s", "5 min"], index=2)
            df = load_delta_flow_day(session, symbol) if symbol else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load delta flow: {exc}")
        return

    _auto_refresh({"Off": 0, "60s": 60, "5 min": 300}[refresh])

    if not symbol:
        st.warning("No symbols configured.")
        return
    if df is None or df.empty:
        st.info(
            "No delta-flow data for this symbol yet. The 5-minute collector "
            "populates it during the regular session (09:30-16:00 ET) once it is "
            "running on the NAS."
        )
        return

    last = df.iloc[-1]
    st.caption(freshness_caption(last["ts"], label="Last 5-min update"))
    c1, c2, c3 = st.columns(3)
    c1.metric("Calls (all)", f"{last['call_notional_all'] / 1e9:,.2f}B")
    c2.metric("Puts (all)", f"{last['put_notional_all'] / 1e9:,.2f}B")
    c3.metric("Spot", f"{last['spot']:g}" if last["spot"] is not None else "n/a")

    st.plotly_chart(_flow_figure(df, symbol), use_container_width=True)


if __name__ == "__main__":
    main()
