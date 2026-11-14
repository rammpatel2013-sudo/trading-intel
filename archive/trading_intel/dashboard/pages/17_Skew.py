"""Skew dashboard — per-name + index time series + VIX-options view.

Thin shell over ``dashboard/skew_data.py``. Three tabs:

1. **Per-name** — sortable sheet of latest skew rows (sorted call-bias-first),
   plus a price-with-RR-band chart per selected ticker that reproduces the MU
   reference image.
2. **Index time series** — the SPX-style chart: 25Δ RR, SDEX, Cboe SKEW, and
   the VIX tail-hedging composite across the chosen lookback.
3. **VIX options** — today's VIX chain rendered as call-wing IV by strike and
   the OI distribution. Shows the structural call skew that drives the
   ``vix_tail_hedging_score``.

Per ADR-003 (revision 2), skew is signal-eligible — but signals come from
``strategies/skew.py`` and surface via the AM Report page. This page is the
descriptor view: regime context and the raw inputs that drive the signals.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.skew_data import (
    index_timeseries,
    per_name_latest,
    per_name_rr_band,
    vix_options_today,
)


def _session() -> Session:
    settings = get_settings()
    from sqlalchemy import create_engine

    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


def _per_name_tab(session: Session) -> None:
    st.subheader("Per-name 25Δ skew (latest day)")
    horizon = st.selectbox(
        "Horizon (DTE)", options=[30, 60, 90, 180, 365], index=0,
        help="Calendar days to expiry the surface was sampled at.",
    )
    df = per_name_latest(session, horizon_dte=horizon)
    if df.empty:
        st.info("No skew rows yet — the EOD job has not run for this horizon.")
        return
    st.dataframe(
        df,
        column_config={
            "rr_25d": st.column_config.NumberColumn("25Δ RR (vol)", format="%.4f"),
            "rr_10d": st.column_config.NumberColumn("10Δ RR (vol)", format="%.4f"),
            "bf_25d": st.column_config.NumberColumn("25Δ BF (vol)", format="%.4f"),
            "rr_25d_pctile_63d": st.column_config.NumberColumn("RR %ile 3M", format="%.3f"),
            "rr_25d_pctile_252d": st.column_config.NumberColumn("RR %ile 1Y", format="%.3f"),
            "vix_beta_60d": st.column_config.NumberColumn("VIX β (60d)", format="%.2f"),
            "rr_25d_abnormal": st.column_config.NumberColumn(
                "Δrr abnormal (pts)", format="%.3f",
                help="Δrr_25d minus β·ΔSDEX — the residual after VIX-β explained move."
            ),
        },
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("Per-name time series (MU-style chart)")
    if "symbol" not in df.columns or df["symbol"].dropna().empty:
        return
    symbol = st.selectbox(
        "Ticker", options=sorted(df["symbol"].dropna().unique().tolist())
    )
    ts_df = per_name_rr_band(session, symbol, horizon_dte=horizon, window=63)
    if ts_df.empty:
        st.info("No time-series rows for this ticker yet.")
        return
    # Price + RR band rendered as two stacked simple charts (no plotly here so
    # the page renders fast on Streamlit Cloud / the NAS).
    if "close" in ts_df.columns:
        st.line_chart(ts_df.set_index("ts")["close"], height=200)
    rr_cols = [c for c in ("rr_25d", "rr_min", "rr_max", "rr_mean") if c in ts_df.columns]
    st.line_chart(ts_df.set_index("ts")[rr_cols], height=200)


def _index_tab(session: Session) -> None:
    st.subheader("Index-level skew time series")
    lookback = st.selectbox(
        "Lookback (days)", options=[90, 180, 365, 730], index=2,
        help="Trailing window to plot.",
    )
    df = index_timeseries(session, lookback_days=lookback)
    if df.empty:
        st.info("No index-skew history yet — the EOD job has not seeded any rows.")
        return
    df_indexed = df.set_index("date")
    st.markdown("**SPX 25Δ RR + SDEX**")
    cols = [c for c in ("spx_rr_25d_30d", "sdex") if c in df_indexed.columns]
    st.line_chart(df_indexed[cols], height=220)
    st.markdown("**Cboe SKEW + VVIX**")
    cols2 = [c for c in ("cboe_skew", "vvix") if c in df_indexed.columns]
    st.line_chart(df_indexed[cols2], height=220)
    st.markdown("**VIX tail-hedging composite (z-sum)**")
    if "vix_tail_hedging_score" in df_indexed.columns:
        st.line_chart(df_indexed["vix_tail_hedging_score"], height=180)


def _vix_options_tab(session: Session) -> None:
    st.subheader("VIX options chain (today)")
    chain = vix_options_today(session)
    if chain.empty:
        st.info("No VIX options snapshot yet.")
        return
    chain["expiration"] = pd.to_datetime(chain["expiration"]).dt.date
    expiry = st.selectbox(
        "Expiry", options=sorted(chain["expiration"].unique()), index=0
    )
    slc = chain.loc[chain["expiration"] == expiry].copy()
    slc = slc.sort_values("strike")
    st.markdown("**IV by strike (calls vs puts)**")
    pivot = slc.pivot_table(index="strike", columns="opt_kind", values="iv")
    st.line_chart(pivot, height=220)
    st.markdown("**Open interest by strike**")
    oi_pivot = slc.pivot_table(index="strike", columns="opt_kind", values="oi", aggfunc="sum")
    st.bar_chart(oi_pivot, height=220)


def main() -> None:
    st.set_page_config(page_title="Skew", layout="wide")
    st.title("Skew")
    st.caption(
        "Per-name + index-level volatility skew. RR = iv_put - iv_call (equity "
        "convention); negative = call bias. Per ADR-003 (revision 2), skew is "
        "signal-eligible; signals surface on the AM Report page."
    )

    session = _session()
    try:
        tab_name, tab_index, tab_vix = st.tabs(["Per-name", "Index time series", "VIX options"])
        with tab_name:
            _per_name_tab(session)
        with tab_index:
            _index_tab(session)
        with tab_vix:
            _vix_options_tab(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
