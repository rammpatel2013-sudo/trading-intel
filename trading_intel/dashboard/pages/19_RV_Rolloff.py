"""RV Roll-off — mechanical realized-vol drift as old days age out of the window.

Reads ``quotes_daily`` closes and projects how the trailing-window realized vol
drifts over the next N sessions purely from the roll-off of past returns (a calm
/ zero-return-tape assumption). Surfaces Doc McGraw's "the big down-days age out
of the 21-day window -> measured RV drifts to a floor -> the floor becomes a
launchpad for systematic (vol-target / CTA) buying" mechanic.

Read-only descriptor page (FlashAlpha rule 4). The math lives in
``prices/realized_vol.py::rv_rolloff_projection``.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.prices.realized_vol import rv_rolloff_projection

st.set_page_config(page_title="RV Roll-off", layout="wide")


# ── DB ─────────────────────────────────────────────────────────────────


@st.cache_resource
def _session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


@st.cache_data(ttl=300)
def _symbols() -> list[str]:
    sess = _session()
    rows = (
        sess.execute(text("SELECT DISTINCT symbol FROM quotes_daily ORDER BY symbol"))
        .scalars()
        .all()
    )
    return list(rows)


@st.cache_data(ttl=300)
def _closes(symbol: str, lookback: int = 260) -> pd.DataFrame:
    sess = _session()
    rows = (
        sess.execute(
            text("""
            SELECT date, close
              FROM quotes_daily
             WHERE symbol = :s
             ORDER BY date DESC
             LIMIT :n
            """),
            {"s": symbol, "n": lookback},
        )
        .mappings()
        .all()
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows]).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── page ───────────────────────────────────────────────────────────────


def main() -> None:
    st.title("RV Roll-off Projection")
    st.caption(
        "Mechanical drift of trailing-window realized vol as past returns age "
        "out of the window (calm-tape assumption). Descriptor, not a signal — "
        "it shows the RV floor that big past moves leave behind, which "
        "systematic vol-target / CTA buying keys off."
    )

    syms = _symbols()
    if not syms:
        st.warning("No rows in quotes_daily yet.")
        return
    default_ix = syms.index("SPX") if "SPX" in syms else 0
    c1, c2, c3 = st.columns([2, 1, 1])
    symbol = c1.selectbox("Symbol", syms, index=default_ix)
    window = c2.slider("RV window (sessions)", 5, 60, 21)
    horizon = c3.slider("Horizon (sessions ahead)", 3, 30, 10)

    df = _closes(symbol)
    if df.empty or len(df) < window + 1:
        st.warning(f"Need at least {window + 1} closes for {symbol}; have {len(df)}.")
        return

    proj = rv_rolloff_projection(df["close"], window=window, horizon=horizon)
    rv_now = float(proj.iloc[0]["projected_rv"])
    floor_row = proj.loc[proj["projected_rv"].idxmin()]
    rv_floor = float(floor_row["projected_rv"])
    floor_off = int(floor_row["session_offset"])
    drops = proj.dropna(subset=["dropped_return"])
    cliff = drops.loc[drops["dropped_return"].abs().idxmax()] if not drops.empty else None

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Current RV", f"{rv_now * 100:.1f}%")
    m2.metric(
        f"Projected floor (+{floor_off}d)",
        f"{rv_floor * 100:.1f}%",
        f"{(rv_floor - rv_now) * 100:+.1f} pts",
        delta_color="inverse",
    )
    m3.metric("As of", df["date"].iloc[-1].strftime("%Y-%m-%d"))
    if cliff is not None:
        m4.metric(
            "Biggest drop-off",
            f"{float(cliff['dropped_return']) * 100:+.2f}%",
            f"ages out +{int(cliff['session_offset'])}d",
        )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=proj["session_offset"],
            y=proj["projected_rv"] * 100,
            mode="lines+markers",
            name="Projected RV",
            line=dict(color="#4c9be8", width=2),
        )
    )
    fig.add_hline(
        y=rv_now * 100,
        line_dash="dot",
        line_color="#888888",
        annotation_text="current",
    )
    fig.update_layout(
        template="plotly_dark",
        height=340,
        title=f"{symbol} — trailing-{window}d RV projected forward (calm tape)",
        xaxis_title="sessions ahead",
        yaxis_title="annualized RV (%)",
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "Assumes future daily returns = 0 (pure roll-off). Real RV won't fall "
        "this far unless the tape actually stays calm — the floor is the "
        "*mechanical* lower bound, and the gap to it is the systematic-flow "
        "tailwind Doc flags around monthly OPEX / RV-window turns."
    )

    with st.expander("Projection table"):
        show = proj.copy()
        show["projected_rv"] = (show["projected_rv"] * 100).round(2)
        show["dropped_return"] = (show["dropped_return"] * 100).round(2)
        show.columns = ["sessions_ahead", "projected_rv_%", "dropped_return_%"]
        st.dataframe(show, hide_index=True, use_container_width=True)


main()
