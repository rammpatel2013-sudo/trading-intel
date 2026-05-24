"""VIX dashboard page - regime, term structure, VRP, vol-of-vol, decomposition reads.

Thin shell over ``dashboard/vix_view.py`` (pure) + the persisted ``vix_data``
rows written by the ``vix_snapshot`` collector. Shows the current VIX level vs.
the regime zones (< 22 carry / 22-32 fragility / > 32 stress; crisis ~ 38.3),
VVIX and the VVIX/VIX vol-of-vol ratio, the variance risk premium (VRP = VIX -
SPX realized vol), the VIX term structure (from stored data, classified
contango / backwardation / flat) and its near-term-stress read (VIX9D/VIX), plus
credit spreads. Interpretation guidance lives in docs/guides/reading-the-vix.md.
Descriptive regime view - not a signal (FlashAlpha rule 4).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.clients.cboe import CboeClient
from trading_intel.config import get_settings
from trading_intel.dashboard.vix_decomp_data import latest_spx_decomposition
from trading_intel.dashboard.vix_view import (
    ZONE_LOW_MAX,
    ZONE_MID_MAX,
    classify_term_structure,
    load_vix_history,
    near_term_stress,
    term_structure_frame,
    term_structure_from_row,
    vvix_vix_ratio,
    zone_caption,
)
from trading_intel.errors import TradingIntelError

_SHAPE_CAPTION = {
    "contango": "Contango (near < far) - calm regime; vol expected to fade.",
    "backwardation": "Backwardation (near > far) - acute front-end stress; protection bid now.",
    "flat": "Flat term structure - transitional.",
}

_FACTOR_LABELS = {
    "sticky_strike": "Sticky strike (mechanical)",
    "parallel_shift": "Parallel shift (regime/fear)",
    "put_gradient": "Put gradient (downside hedge)",
    "call_gradient": "Call gradient (upside)",
    "down_convexity": "Downside convexity (tail)",
    "up_convexity": "Upside convexity (lotto)",
}


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _metric(value: float | None, fmt: str = "{:.2f}") -> str:
    return fmt.format(value) if value is not None and not pd.isna(value) else "—"


def _term_figure(term: pd.DataFrame, shape: str | None) -> go.Figure:
    title = "VIX term structure (stored)"
    if shape:
        title += f" - {shape}"
    fig = go.Figure(go.Scatter(x=term["tenor"], y=term["level"], mode="lines+markers"))
    fig.update_layout(
        title=title, template="plotly_dark", height=320,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_yaxes(title_text="Implied vol")
    return fig


def _history_figure(hist: pd.DataFrame) -> go.Figure:
    fig = go.Figure(go.Scatter(x=hist["date"], y=hist["vix"], mode="lines", name="VIX"))
    fig.add_hline(y=ZONE_LOW_MAX, line_color="#2ecc71", line_dash="dot",
                  annotation_text="22 carry/fragility")
    fig.add_hline(y=ZONE_MID_MAX, line_color="#e74c3c", line_dash="dot",
                  annotation_text="32 stress")
    fig.update_layout(
        title="VIX history + regime zones", template="plotly_dark", height=340,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    return fig


def _vrp_figure(hist: pd.DataFrame) -> go.Figure | None:
    vrp = hist[["date", "vrp"]].dropna()
    if vrp.empty:
        return None
    fig = go.Figure(go.Scatter(x=vrp["date"], y=vrp["vrp"], mode="lines", name="VRP"))
    fig.add_hline(y=0.0, line_color="#e74c3c", line_dash="dot",
                  annotation_text="0 (implied = realized)")
    fig.update_layout(
        title="Variance risk premium (VIX - SPX realized vol)",
        template="plotly_dark", height=340,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_yaxes(title_text="Vol points")
    return fig


def _safe_decomp(session: Session):
    """Best-effort SPX decomposition; None if oi_chain_eod is unreachable."""
    try:
        return latest_spx_decomposition(session)
    except (TradingIntelError, SQLAlchemyError):
        return None


def _decomp_figure(decomp) -> go.Figure:
    items = list(decomp.factors.items())[::-1]  # sticky strike rendered on top
    labels = [_FACTOR_LABELS[k] for k, _ in items]
    values = [v for _, v in items]
    colors = ["#2ecc71" if v >= 0 else "#e74c3c" for v in values]
    fig = go.Figure(go.Bar(x=values, y=labels, orientation="h", marker_color=colors))
    fig.update_layout(
        title="VIX day-over-day decomposition (vol points)",
        template="plotly_dark", height=360,
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    fig.update_xaxes(title_text="Contribution (vol points)")
    return fig


def _render_decomposition(result) -> None:
    if result is None:
        st.caption("Decomposition unavailable (oi_chain_eod not reachable).")
        return
    d = result.decomposition
    if d is None:
        st.info(
            "Accumulating history: the decomposition needs 2 consecutive SPX "
            f"oi_chain_eod snapshots; have {result.days_available}. It lights up "
            "after the next EOD run."
        )
        return
    st.plotly_chart(_decomp_figure(d), use_container_width=True)
    st.caption(
        f"Dominant: **{_FACTOR_LABELS[d.dominant]}**. {d.regime_read()}. "
        f"({result.prior:%Y-%m-%d} -> {result.as_of:%Y-%m-%d})"
    )


def main() -> None:
    st.set_page_config(page_title="VIX", page_icon="📈", layout="wide")
    st.title("📈 VIX — volatility regime")

    try:
        factory = _session_factory()
        with factory() as session:
            hist = load_vix_history(session, days=180)
            decomp_result = _safe_decomp(session)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load VIX history: {exc}")
        return

    if hist.empty:
        st.info(
            "No vix_data yet. The vix_snapshot collector writes one row per day "
            "(FRED VIX/credit + CBOE VVIX/term structure) — this lights up after "
            "the first run."
        )
        return

    latest = hist.iloc[-1]
    vix = latest["vix"]
    ratio = vvix_vix_ratio(latest["vvix"], vix)
    nts = near_term_stress(latest["vix9d"], vix)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("VIX", _metric(vix))
    c2.metric("Zone", str(latest["vega_zone"] or "—"))
    c3.metric("VVIX", _metric(latest["vvix"], "{:.1f}"))
    c4.metric("VVIX/VIX", _metric(ratio, "{:.2f}"))
    c5.metric("VRP", _metric(latest["vrp"], "{:+.2f}"))
    c6.metric("VIX9D/VIX", _metric(nts, "{:.2f}"))
    st.caption(zone_caption(vix))

    # Term structure: prefer persisted tenors; fall back to a live CBOE fetch.
    term = term_structure_from_row(latest)
    if term.empty:
        try:
            term = term_structure_frame(CboeClient().term_structure())
        except Exception:  # live CBOE fetch is best-effort on the page
            term = term_structure_frame(None)
    shape = classify_term_structure(term)

    left, right = st.columns(2)
    with left:
        st.plotly_chart(_history_figure(hist), use_container_width=True)
    with right:
        if not term.empty:
            st.plotly_chart(_term_figure(term, shape), use_container_width=True)
            if shape:
                st.caption(_SHAPE_CAPTION[shape])
        else:
            st.caption("Term structure unavailable.")

    vrp_fig = _vrp_figure(hist)
    if vrp_fig is not None:
        st.plotly_chart(vrp_fig, use_container_width=True)
    else:
        st.caption("VRP not available yet (needs an SPX rv20 row in quotes_daily).")

    st.subheader("VIX decomposition - mechanical vs. true fear")
    _render_decomposition(decomp_result)

    with st.expander("How to read this", expanded=False):
        st.markdown(
            "- **Zone**: < 22 carry (sell vol), 22-32 fragility, > 32 stress.\n"
            "- **Term structure**: contango = calm; backwardation (VIX9D/VIX > 1) "
            "= acute near-term stress.\n"
            "- **VRP** (VIX - realized): positive = implied richer than realized "
            "(vol sellers paid); compressing toward 0 = realized catching up.\n"
            "- **VVIX/VIX**: elevated at a low VIX = latent fragility under a quiet "
            "tape.\n\n"
            "Full interpretation guide (incl. the CBOE decomposition): "
            "`docs/guides/reading-the-vix.md`."
        )


if __name__ == "__main__":
    main()
