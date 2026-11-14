"""Market-timing dashboard - dealer-gamma regime + vol regime + seasonality.

Synthesizes the gamma-regime read (greeks.gamma_regime via oi_chain_eod) and the
vol regime (VIX zone / term-structure shape / VRP from vix_data) into a single
descriptive market bias, with a seasonality overlay. A slot is reserved for true
market internals ($ADD) - FMP free has no breadth feed. Descriptive regime view,
not a signal (FlashAlpha rule 4).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.freshness import freshness_caption
from trading_intel.dashboard.gamma_regime_data import latest_spx_gamma_regime
from trading_intel.dashboard.market_timing import market_bias
from trading_intel.dashboard.seasonality import seasonal_context
from trading_intel.dashboard.vix_decomp_data import latest_spx_decomposition
from trading_intel.dashboard.vix_view import (
    classify_term_structure,
    load_vix_history,
    term_structure_from_row,
    vvix_vix_ratio,
)
from trading_intel.errors import TradingIntelError


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _safe(fn: Callable[..., object], *args: object, **kwargs: object) -> object | None:
    """Best-effort loader call; None on any DB/domain error."""
    try:
        return fn(*args, **kwargs)
    except (TradingIntelError, SQLAlchemyError):
        return None


def _fmt(value: object, fmt: str = "{:.2f}") -> str:
    if value is None or value != value:  # None or NaN
        return "—"
    return fmt.format(value)


def _zone_md(zone: object) -> str:
    """Streamlit coloured chip for the VIX zone (green carry / amber mid / red stress)."""
    color = {"low": "green", "mid": "orange", "high": "red"}.get(str(zone or "").lower(), "gray")
    return f":{color}[{zone or '—'}]"


def _regime_md(regime: object) -> str:
    """Streamlit coloured chip for the gamma regime (green long / red short)."""
    r = str(regime or "").lower()
    color = "green" if "long" in r else "red" if "short" in r else "gray"
    return f":{color}[{regime or '—'}]"


def main() -> None:
    st.set_page_config(page_title="Market Timing", page_icon="🧭", layout="wide")
    st.title("🧭 Market timing — regime synthesis")

    factory = _session_factory()
    with factory() as session:
        gamma = _safe(latest_spx_gamma_regime, session)
        hist = _safe(load_vix_history, session, days=180)
        decomp = _safe(latest_spx_decomposition, session)

    vol_zone = term_shape = None
    latest = None
    if hist is not None and not hist.empty:
        latest = hist.iloc[-1]
        vol_zone = latest["vega_zone"]
        term_shape = classify_term_structure(term_structure_from_row(latest))

    gamma_regime = gamma.regime if gamma is not None else None
    bias = market_bias(gamma_regime, vol_zone, term_shape)
    season = seasonal_context(date.today())

    st.subheader(f"Bias: {bias.label}")
    st.caption(bias.detail + "  ·  Descriptive regime read — not a signal.")
    fresh = latest["date"] if latest is not None else None
    st.caption(freshness_caption(fresh, label="VIX data"))

    col_g, col_v, col_s = st.columns(3)

    with col_g:
        st.markdown("**Gamma regime**")
        if gamma is None:
            st.caption("No SPX oi_chain_eod snapshot yet.")
        else:
            st.markdown(f"Regime: {_regime_md(gamma.regime)}")
            st.metric("Net GEX", _fmt(gamma.net_gex, "{:,.0f}"))
            st.metric("Flip", _fmt(gamma.flip, "{:.0f}"))
            st.caption(
                f"Walls: put {_fmt(gamma.put_wall, '{:.0f}')} / "
                f"call {_fmt(gamma.call_wall, '{:.0f}')}. {gamma.regime_read()}"
            )

    with col_v:
        st.markdown("**Vol regime**")
        if latest is None:
            st.caption("No vix_data yet.")
        else:
            st.metric("VIX", _fmt(latest["vix"]))
            st.markdown(f"Zone: {_zone_md(vol_zone)}")
            st.metric("Term structure", str(term_shape or "—"))
            st.metric("VRP", _fmt(latest["vrp"], "{:+.2f}"))
            ratio = vvix_vix_ratio(latest["vvix"], latest["vix"])
            cap = f"VVIX/VIX {_fmt(ratio)}."
            if decomp is not None and decomp.decomposition is not None:
                cap += " " + decomp.decomposition.regime_read()
            st.caption(cap)

    with col_s:
        st.markdown("**Seasonality**")
        st.metric("Half-year", season.half_label)
        st.metric("Sell-in-May", "yes" if season.in_sell_in_may else "no")
        st.metric("Weekday", season.weekday)
        st.caption(season.note)

    st.divider()
    st.markdown("**Market internals ($ADD / breadth)**")
    st.caption(
        "Reserved — FMP free tier has no NYSE advance/decline feed. This slot is "
        "wired for a true $ADD source (ThinkOrSwim/CBOE) when one is available."
    )

    with st.expander("How to read this", expanded=False):
        st.markdown(
            "- **Bias** blends the dealer-gamma regime, the VIX zone, and the "
            "term-structure shape into one read.\n"
            "- **Negative gamma / backwardation / high VIX** → risk-off, trending, "
            "bounces lower-confidence.\n"
            "- **Positive gamma + low VIX + contango** → calm, range-bound, a "
            "premium-selling environment.\n"
            "- **Transitional** → spot on the gamma flip; regime can tip.\n"
            "- Seasonality is a weak prior, not a trigger. Nothing here is a signal "
            "(FlashAlpha rule 4)."
        )


if __name__ == "__main__":
    main()
