"""Vol Surface — the SPX/QQQ/SPY volatility-surface *changes* board.

Reads the banked ``surface_snapshots`` (near-money per-STRIKE IV grid) and diffs today vs
the prior banked day:

- IV surface heatmap + interactive 3D surface (strike x expiry x IV).
- Fixed-STRIKE vol *changes* heatmap (today − prior, vol points) — each listed contract
  vs its own prior-day mark, so nothing is smeared by spot sliding along the skew.
- Front-expiry skew (live vs prior) and the ATM term structure (live vs prior).

Regime descriptor only (CLAUDE.md rule 4) — no signals. Data lands after
``scheduler/jobs/surface_snapshots.py`` runs (EOD 17:08 ET).
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.vol_surface_data import (
    available_symbols,
    changes_pivot,
    load_surface,
    surface_pivot,
    two_latest_dates,
)

_DARK = "plotly_dark"
_GOLD = "#f6c343"
_GREY = "#94a3b8"
_IV_SCALE = [[0.0, "#0e4a5e"], [0.5, "#e0b13a"], [1.0, "#f9e27a"]]  # teal -> gold


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _atm_term(df, spot):  # noqa: ANN001, ANN202
    """Per expiry, the row whose strike is nearest ``spot`` — the ATM term structure."""
    if df is None or df.empty:
        return None
    sp = spot
    if sp is None:
        sp = float(df["spot"].dropna().iloc[0]) if df["spot"].notna().any() else float(df["strike"].median())
    d = df.dropna(subset=["iv"]).assign(_d=(df["strike"] - sp).abs())
    return d.sort_values(["dte", "_d"]).groupby("dte", as_index=False).first().sort_values("dte")


def main() -> None:
    st.set_page_config(page_title="Vol Surface", page_icon="🌐", layout="wide")
    st.title("🌐 Volatility surface — changes")
    st.caption(
        "Near-money per-STRIKE IV surface for the index ETFs, today vs the prior banked day. "
        "Changes are FIXED-STRIKE (each listed contract vs its own prior-day mark), so a move "
        "is the real re-mark of that contract, not a delta bucket sliding on the skew. "
        "Descriptor only (FlashAlpha rule)."
    )

    factory = _session_factory()
    try:
        with factory() as session:
            syms = available_symbols(session)
            if not syms:
                st.info(
                    "No surface data yet. Run the collector:\n\n"
                    "`.venv\\Scripts\\python -m trading_intel.scheduler.jobs.surface_snapshots`\n\n"
                    "(needs a live chain — run after the close. Then it banks EOD 17:08 ET.)"
                )
                return
            default = "SPX" if "SPX" in syms else syms[0]
            symbol = st.selectbox("Symbol", syms, index=syms.index(default))
            dates = two_latest_dates(session, symbol)
            ts_today = dates[0]
            ts_prior = dates[1] if len(dates) > 1 else None
            df_today = load_surface(session, symbol, ts_today)
            df_prior = load_surface(session, symbol, ts_prior) if ts_prior else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load surface: {exc}")
        return

    if df_today.empty:
        st.info("Latest snapshot has no rows yet.")
        return

    spot = float(df_today["spot"].dropna().iloc[0]) if df_today["spot"].notna().any() else None
    cur = f"{ts_today}"
    prior = f"{ts_prior}" if ts_prior else "—"
    st.markdown(
        f"**Current:** {cur}  ·  **Prior:** {prior}  ·  "
        f"**Spot:** {round(spot) if spot is not None else 'n/a'}"
    )
    if ts_prior is None:
        st.info("Only one snapshot banked so far — changes appear once a second day is collected.")

    iv = surface_pivot(df_today)             # strike (idx, high->low) x dte (cols), vol %
    chg = changes_pivot(df_today, df_prior)  # vol points, aligned on (expiry, strike)

    # ── Row 1: IV surface heatmap + 3D surface ───────────────────────────
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("IV surface")
        hm = go.Figure(
            go.Heatmap(
                z=iv.values, x=[f"{c}d" for c in iv.columns], y=iv.index,
                colorscale=_IV_SCALE, colorbar=dict(title="IV%"),
                hovertemplate="K %{y} · %{x}<br>IV %{z:.2f}%<extra></extra>",
            )
        )
        hm.update_layout(template=_DARK, height=380, margin=dict(l=10, r=10, t=10, b=10),
                         yaxis_title="strike", xaxis_title="expiry")
        st.plotly_chart(hm, use_container_width=True)
    with c2:
        st.subheader("3D surface")
        surf = go.Figure(
            go.Surface(
                z=iv.values, x=[f"{c}d" for c in iv.columns], y=iv.index,
                colorscale=_IV_SCALE, showscale=False,
            )
        )
        surf.update_layout(
            template=_DARK, height=380, margin=dict(l=0, r=0, t=0, b=0),
            scene=dict(
                xaxis_title="expiry", yaxis_title="strike", zaxis_title="IV%",
                camera=dict(eye=dict(x=1.5, y=-1.5, z=0.9)),
            ),
        )
        st.plotly_chart(surf, use_container_width=True)

    # ── Row 2: changes heatmap ───────────────────────────────────────────
    st.subheader("Fixed-strike vol changes (today − prior, vol pts)")
    if chg.empty:
        st.info("No prior day to diff yet.")
    else:
        cap = max(0.25, float(abs(chg.values[chg.notna().values]).max()) if chg.notna().values.any() else 1.0)
        chm = go.Figure(
            go.Heatmap(
                z=chg.values, x=[f"{c}d" for c in chg.columns], y=chg.index,
                colorscale="RdBu", zmid=0, zmin=-cap, zmax=cap, colorbar=dict(title="Δvol"),
                hovertemplate="K %{y} · %{x}<br>Δvol %{z:+.2f}<extra></extra>",
            )
        )
        chm.update_layout(template=_DARK, height=340, margin=dict(l=10, r=10, t=10, b=10),
                          yaxis_title="strike", xaxis_title="expiry")
        st.plotly_chart(chm, use_container_width=True)

    # ── Row 3: front skew + term structure ───────────────────────────────
    c3, c4 = st.columns(2)
    with c3:
        st.subheader("Front-expiry skew (live vs prior)")
        near_exp = df_today.sort_values("dte")["expiry_date"].iloc[0]
        ft = df_today[df_today["expiry_date"] == near_exp].sort_values("strike")
        fig = go.Figure()
        fig.add_scatter(x=ft["strike"], y=ft["iv"] * 100, mode="lines+markers",
                        name="live", line=dict(color=_GOLD, width=3))
        if df_prior is not None and not df_prior.empty:
            fp = df_prior[df_prior["expiry_date"] == near_exp].sort_values("strike")
            if not fp.empty:
                fig.add_scatter(x=fp["strike"], y=fp["iv"] * 100, mode="lines",
                                name="prior", line=dict(color=_GREY, width=2, dash="dash"))
        fig.update_layout(template=_DARK, height=330, margin=dict(l=10, r=10, t=10, b=10),
                          xaxis_title="strike", yaxis_title="IV%",
                          legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)
    with c4:
        st.subheader("ATM term structure (live vs prior)")
        at = _atm_term(df_today, spot)
        fig2 = go.Figure()
        if at is not None and not at.empty:
            fig2.add_scatter(x=at["dte"], y=at["iv"] * 100, mode="lines+markers",
                             name="live", line=dict(color=_GOLD, width=3))
        ap = _atm_term(df_prior, None) if df_prior is not None else None
        if ap is not None and not ap.empty:
            fig2.add_scatter(x=ap["dte"], y=ap["iv"] * 100, mode="lines",
                             name="prior", line=dict(color=_GREY, width=2, dash="dash"))
        fig2.update_layout(template=_DARK, height=330, margin=dict(l=10, r=10, t=10, b=10),
                           xaxis_title="days to expiry", yaxis_title="ATM IV%",
                           legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig2, use_container_width=True)

    with st.expander("Raw surface (IV %, strike × expiry-DTE)"):
        st.dataframe(iv, use_container_width=True)


main()
