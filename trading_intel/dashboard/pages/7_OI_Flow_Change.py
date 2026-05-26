"""OI & flow change page — day-over-day positioning, by strike.

For one watchlist symbol, compares the two most recent EOD wide-chain snapshots
(``oi_chain_eod``) and surfaces, per strike: ΔOI (today - yesterday), today's
volume, conversion (|ΔOI| / volume — new positioning vs churn), the vendor's
native OI change, and each strike's net-signed GEX contribution and its change.
Rolls up to total ΔGEX and call-vs-put ΔOI with a descriptive read-through.

Thin shell over ``dashboard/oi_changes.py`` (pure, unit-tested). Every panel is
a regime descriptor — not a trade signal (FlashAlpha rule 4). Lights up once the
``oi_chain_eod`` collector has stored at least two EOD snapshots.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.oi_changes import (
    load_oi_change_frame,
    summarize_oi_change,
    top_oi_changes,
)
from trading_intel.errors import TradingIntelError
from trading_intel.memory.models import OiChainEod

_POS = "#5dade2"
_NEG = "#e74c3c"
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
    preferred = [s for s in _PREFERRED if s in symbols]
    rest = [s for s in symbols if s not in _PREFERRED]
    return preferred + rest


def _d_gex_figure(top: object, symbol: str) -> go.Figure:
    """Diverging bar of per-strike ΔGEX contribution for the top-changed strikes."""
    labels = [f"{int(s)}{cp}" for s, cp in zip(top["strike"], top["cp"], strict=False)]
    colors = [_POS if v >= 0 else _NEG for v in top["d_gex_contrib"]]
    fig = go.Figure(
        go.Bar(x=labels, y=top["d_gex_contrib"], marker_color=colors, name="ΔGEX")
    )
    fig.update_layout(
        title=f"{symbol} — biggest per-strike ΔGEX vs prior session",
        template="plotly_dark", height=360,
        margin={"l": 10, "r": 10, "t": 50, "b": 10}, showlegend=False,
    )
    fig.update_xaxes(title_text="Strike (C/P)")
    fig.update_yaxes(title_text="Δ net-signed GEX")
    return fig


def main() -> None:
    st.set_page_config(page_title="OI & Flow Change", page_icon="🔄", layout="wide")
    settings = get_settings()
    symbols = _ordered_symbols(list(settings.watchlist_symbols))

    st.title("🔄 OI & flow change — day over day")
    st.caption(
        "Per-strike ΔOI, volume, conversion (|ΔOI| / volume) and ΔGEX vs the prior "
        "EOD snapshot. Regime descriptors only — not trade signals (FlashAlpha rule)."
    )

    symbol = st.sidebar.selectbox("Symbol", symbols, index=0 if symbols else None)
    rank_by = st.sidebar.radio("Rank strikes by", ["d_oi", "d_gex_contrib"], index=0)
    top_n = st.sidebar.slider("Show top N strikes", min_value=5, max_value=50, value=15, step=5)
    if not symbol:
        st.warning("No symbols configured in the watchlist.")
        return

    try:
        factory = _session_factory()
        with factory() as session:
            frame = load_oi_change_frame(session, symbol)
            latest_ts = session.execute(
                select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == symbol)
            ).scalar_one_or_none()
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load OI change for {symbol}: {exc}")
        return

    if frame is None:
        st.info(
            f"Need at least two EOD snapshots for {symbol}. The oi_chain_eod collector "
            "writes one per trading day after the close — this lights up on the second day."
        )
        return

    st.caption(
        freshness_caption(latest_ts.date() if latest_ts else None, label="Snapshot pulled")
    )
    summary = summarize_oi_change(frame)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total ΔGEX", f"{summary.total_d_gex:,.0f}")
    c2.metric("Call ΔOI", f"{summary.call_d_oi:,.0f}")
    c3.metric("Put ΔOI", f"{summary.put_d_oi:,.0f}")
    c4.metric("Mean ΔIV", f"{summary.mean_d_iv:+.4f}")
    st.caption(summary.note)

    top = top_oi_changes(frame, by=rank_by, n=top_n, sort_by_strike=True)
    st.plotly_chart(_d_gex_figure(top, symbol), use_container_width=True)

    st.subheader(f"Top {len(top)} strikes by |{rank_by}| (low strike -> high)")
    display = top.rename(
        columns={
            "oi_change_vendor": "oi_chg (convex)",
            "d_oi": "ΔOI (ours)",
            "d_iv": "ΔIV",
            "positioning": "positioning read",
            "d_gex_contrib": "ΔGEX",
            "gex_contrib_curr": "GEX contrib",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
