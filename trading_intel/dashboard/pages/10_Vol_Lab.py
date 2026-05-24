"""Volatility Lab v2 - per-symbol IV-surface explorer + IV-HV screener.

Symbol + snapshot-date dropdowns over stored oi_chain_eod: a 3D IV surface
(moneyness x DTE, strikes in hover), an expiry dropdown + front/next/far smile
overlay, constant-maturity 30/60/90d ATM IV, the ATM term structure + forward
vol, an auto surface "Read" (synthesis.surface_report), day-over-day sticky-strike
changes, a metric history time series, and the IV-HV rich/cheap screener.
Interpretation per docs/guides/reading-the-vol-surface.md. Descriptive - rule 4.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.iv_hv_screener import iv_hv_table
from trading_intel.dashboard.vol_lab_data import (
    chain_for_date,
    list_symbols,
    prev_curr_spx_chains,
    snapshot_dates,
)
from trading_intel.errors import ComputationError, TradingIntelError
from trading_intel.greeks.surface import build_delta_surface, build_surface_grid, forward_vol
from trading_intel.synthesis.surface_report import interpret_surface, surface_metrics


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _atm_col(surf) -> int:
    return int(np.argmin(np.abs(surf.moneyness - 1.0)))


def _surface_figure(surf, spot: float) -> go.Figure:
    strikes = np.round(surf.moneyness * spot).astype(int)
    customdata = np.tile(strikes, (surf.dte.size, 1))
    fig = go.Figure(
        go.Surface(
            x=surf.moneyness, y=surf.dte, z=surf.iv * 100.0, customdata=customdata,
            colorscale="Viridis", colorbar={"title": "IV %"},
            hovertemplate="K=%{customdata} (m=%{x:.3f})<br>DTE=%{y}<br>IV=%{z:.1f}%<extra></extra>",
        )
    )
    fig.update_layout(
        title="Implied-vol surface (moneyness x DTE)", template="plotly_dark", height=520,
        scene={"xaxis_title": "Moneyness K/S", "yaxis_title": "DTE", "zaxis_title": "IV %"},
        margin={"l": 0, "r": 0, "t": 50, "b": 0},
    )
    return fig


def _smile_overlay(surf, spot: float, picked_idx: int) -> go.Figure:
    fig = go.Figure()
    n = surf.dte.size
    picks = {"picked": picked_idx, "front": 0, "next": min(1, n - 1), "far": n - 1}
    seen: set[int] = set()
    for label, i in picks.items():
        if i in seen:
            continue
        seen.add(i)
        strikes = np.round(surf.moneyness * spot).astype(int)
        fig.add_trace(go.Scatter(
            x=surf.moneyness, y=surf.iv[i] * 100.0, mode="lines",
            name=f"{label} ({int(surf.dte[i])}d)", customdata=strikes,
            hovertemplate="K=%{customdata} (m=%{x:.3f}) IV=%{y:.1f}%<extra></extra>",
        ))
    fig.add_vline(x=1.0, line_color="#888", line_dash="dot", annotation_text=f"ATM (spot {spot:,.0f})")
    fig.update_layout(
        title="Smile overlay (front / next / far + selected)", template="plotly_dark", height=360,
        xaxis_title="Moneyness K/S", yaxis_title="IV %",
        margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    return fig


def _term_figure(dsurf) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dsurf.dte, y=dsurf.atm_iv * 100.0, mode="lines+markers", name="ATM IV"))
    fwd = forward_vol(dsurf.dte, dsurf.atm_iv) * 100.0
    fig.add_trace(go.Scatter(x=dsurf.dte, y=fwd, mode="lines+markers", name="Forward vol"))
    fig.update_layout(
        title="ATM term structure + forward vol", template="plotly_dark", height=340,
        xaxis_title="DTE", yaxis_title="Vol %", margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    return fig


def _history_figure(session: Session, symbol: str, dates: list) -> go.Figure | None:
    recs = []
    for ts in sorted(dates)[-30:]:
        loaded = chain_for_date(session, symbol, ts)
        if loaded is None:
            continue
        chain, _spot = loaded
        try:
            m = surface_metrics(build_delta_surface(chain))
        except ComputationError:
            continue
        front = m["per_expiry"][0]
        recs.append({
            "date": ts, "atm": front["atm"] * 100.0,
            "skew_25d": front["skew_25d"] * 100.0, "term_slope": m["term_slope"] * 100.0,
        })
    if len(recs) < 2:
        return None
    df = pd.DataFrame(recs)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["atm"], mode="lines+markers", name="Front ATM IV"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["skew_25d"], mode="lines+markers", name="25d skew"))
    fig.add_trace(go.Scatter(x=df["date"], y=df["term_slope"], mode="lines+markers", name="Term slope"))
    fig.update_layout(
        title="Surface metrics over time (vol pts)", template="plotly_dark", height=340,
        xaxis_title="Date", yaxis_title="Vol pts", margin={"l": 10, "r": 10, "t": 50, "b": 10},
    )
    return fig


def main() -> None:
    st.set_page_config(page_title="Vol Lab", page_icon="🌊", layout="wide")
    st.title("🌊 Volatility Lab")

    factory = _session_factory()
    try:
        with factory() as session:
            symbols = list_symbols(session)
            if not symbols:
                st.info("No oi_chain_eod data yet. The vol lab lights up after the EOD chain collector runs.")
                return
            default_i = symbols.index("SPX") if "SPX" in symbols else 0
            symbol = st.selectbox("Symbol", symbols, index=default_i)
            dates = snapshot_dates(session, symbol)
            date_sel = st.selectbox("Snapshot date", dates, index=0, format_func=lambda d: f"{d:%Y-%m-%d}")
            loaded = chain_for_date(session, symbol, date_sel)
            pair = prev_curr_spx_chains(session, symbol=symbol)
            screener = iv_hv_table(session, get_settings().watchlist_symbols)
            hist_fig = _history_figure(session, symbol, dates)
    except (TradingIntelError, SQLAlchemyError) as exc:
        st.error(f"Could not load data: {exc}")
        return

    if loaded is None:
        st.warning(f"No usable chain for {symbol} on {date_sel:%Y-%m-%d}.")
        return
    chain, spot = loaded
    st.caption(f"{symbol} · spot ~{spot:,.2f} · snapshot {date_sel:%Y-%m-%d}")

    if st.button(f"⟳ Pull live now ({symbol})"):
        from trading_intel.dashboard.live_refresh import pull_live_symbol

        with st.spinner(f"Pulling live {symbol} from Convex…"):
            with factory() as lsession:
                status = pull_live_symbol(lsession, symbol, settings=get_settings())
        st.success(f"Live pull: {status} — select today's snapshot above to view it.")
        st.rerun()

    try:
        surf = build_surface_grid(chain, spot)
    except ComputationError as exc:
        st.warning(f"Surface unavailable: {exc}")
        surf = None

    if surf is not None:
        # Constant-maturity ATM IV (interpolated).
        atm_by_dte = surf.iv[:, _atm_col(surf)]
        c1, c2, c3 = st.columns(3)
        for col, t in zip((c1, c2, c3), (30, 60, 90)):
            cm = float(np.interp(t, surf.dte, atm_by_dte)) * 100.0
            col.metric(f"CM {t}d ATM IV", f"{cm:.1f}%" if np.isfinite(cm) else "—")

        st.plotly_chart(_surface_figure(surf, spot), use_container_width=True)

        dte_opts = [int(x) for x in surf.dte]
        picked = st.selectbox("Focus expiry (DTE)", dte_opts, index=0)
        picked_idx = dte_opts.index(picked)
        left, right = st.columns(2)
        with left:
            st.plotly_chart(_smile_overlay(surf, spot, picked_idx), use_container_width=True)
        with right:
            try:
                dsurf = build_delta_surface(chain)
                st.plotly_chart(_term_figure(dsurf), use_container_width=True)
            except ComputationError as exc:
                st.caption(f"Term structure unavailable: {exc}")

        # Auto surface "Read" (deterministic, grounded in the desk methodology).
        try:
            st.markdown(interpret_surface(surface_metrics(build_delta_surface(chain))))
        except ComputationError:
            pass

    st.divider()
    st.subheader("Surface + flow report")
    st.caption("Interpretive desk note (The Read / The Flow / Speculation vs Hedging) via the LLM, grounded in the playbooks.")
    live_flow = st.checkbox("Pull live flow from Convex (else use the stored snapshot)", value=False)
    if st.button("Generate report"):
        from trading_intel.dashboard.report_data import generate_surface_flow_report

        try:
            from trading_intel.synthesis.llm import OllamaProvider

            llm = OllamaProvider(get_settings())
        except Exception:
            llm = None
        with st.spinner("Generating surface + flow report (LLM)…"):
            with factory() as rsession:
                report_md = generate_surface_flow_report(
                    rsession, symbol, settings=get_settings(), llm=llm, prefer_live=live_flow
                )
        st.markdown(report_md)

    st.divider()
    st.subheader("Vol surface + changes by expiry (centered at 50Δ)")
    from trading_intel.greeks.surface_panel import (
        centered_frame,
        next_weekly_expiries,
        surface_panel,
    )

    all_exps = sorted(set(chain["expiration"].dt.date))
    defaults = next_weekly_expiries(chain, n=3) or all_exps[:3]
    picks: list = []
    for i, ecol in enumerate(st.columns(3)):
        if not all_exps:
            break
        dflt = defaults[i] if i < len(defaults) else all_exps[min(i, len(all_exps) - 1)]
        picks.append(ecol.selectbox(
            f"Expiry {i + 1}", all_exps, index=all_exps.index(dflt),
            key=f"vp_exp_{i}", format_func=lambda d: f"{d:%d-%b-%y}",
        ))
    picks = list(dict.fromkeys(picks))  # dedupe, keep order

    prev_chain = pair[0] if pair is not None else None
    panels = surface_panel(chain, prev_chain, picks) if picks else []
    if not panels:
        st.caption("No usable expiries for the panel (need strikes on both wings).")
    else:
        st.markdown(
            "<style>[data-testid='stTable'] td,[data-testid='stTable'] th"
            "{font-size:0.70rem;padding:1px 7px;line-height:1.15;}</style>",
            unsafe_allow_html=True,
        )
        ivf = centered_frame(panels, "iv")
        if prev_chain is None:
            st.markdown("**Vol surface — today's IV % by Δ** (Δ-change columns need a 2nd snapshot)")
            st.table(ivf.round(2))
        else:
            held = st.radio(
                "Change columns — hold constant:",
                ["Fixed delta (sticky-delta)", "Fixed strike (sticky-strike)"],
                horizontal=True,
            )
            kind = "delta" if held.startswith("Fixed delta") else "strike"
            chf = centered_frame(panels, kind)
            combined = ivf.rename(columns={c: f"{c} IV" for c in ivf.columns}).join(
                chf.rename(columns={c: f"{c} Δ" for c in chf.columns})
            )
            change_cols = [f"{c} Δ" for c in chf.columns]

            absvals = np.abs(combined[change_cols].to_numpy(dtype=float))
            vmax = float(np.nanmax(absvals)) if absvals.size and not np.all(np.isnan(absvals)) else 0.0

            def _css(col):
                out = []
                for v in col:
                    if v != v or vmax <= 0:  # NaN or no scale
                        out.append("")
                        continue
                    a = 0.15 + 0.6 * min(abs(v) / vmax, 1.0)
                    if v > 0:
                        out.append(f"background-color: rgba(46,204,113,{a:.2f})")
                    elif v < 0:
                        out.append(f"background-color: rgba(231,76,60,{a:.2f})")
                    else:
                        out.append("")
                return out

            styler = combined.style.format("{:.2f}", na_rep="").apply(_css, subset=change_cols)
            st.markdown(
                f"**Vol surface (IV %) + change vs prior ({held}, vol pts) — green up / red down**"
            )
            st.table(styler)  # full table, no internal scroll

            chf_delta = centered_frame(panels, "delta")  # fixed-delta change (today - prior)
            yest = ivf - chf_delta  # prior-day IV levels, by delta
            lfig = go.Figure()
            palette = ["#3498db", "#e67e22", "#9b59b6", "#1abc9c"]
            for i, c in enumerate(ivf.columns):
                col = palette[i % len(palette)]
                lfig.add_trace(go.Scatter(
                    x=list(ivf.index), y=ivf[c], mode="lines", name=f"{c} today",
                    line={"color": col, "width": 2},
                ))
                lfig.add_trace(go.Scatter(
                    x=list(yest.index), y=yest[c], mode="lines", name=f"{c} prior",
                    line={"color": col, "width": 1, "dash": "dot"},
                ))
            lfig.update_layout(
                title="IV by Δ — today (solid) vs prior (dotted), per expiry",
                template="plotly_dark", height=420,
                xaxis_title="Δ (put 5→50 · ATM · 50→5 call)", yaxis_title="IV %",
                margin={"l": 10, "r": 10, "t": 50, "b": 10},
            )
            st.plotly_chart(lfig, use_container_width=True)
            st.caption(
                "Table: today's IV by Δ + change vs prior (green up / red down). Chart: the IV "
                "curves themselves — today (solid) vs prior (dotted), same colour per expiry, so "
                "you see how each expiry's skew shifted. 6 lines = 3 expiries × today/prior."
            )

    if hist_fig is not None:
        st.divider()
        st.subheader("History")
        st.plotly_chart(hist_fig, use_container_width=True)

    st.divider()
    st.subheader("IV-HV screener (rich vs cheap)")
    if screener is None or screener.empty:
        st.caption("No IV-HV rows yet (needs oi_chain_eod + quotes_daily rv).")
    else:
        disp = screener.copy()
        for c in ("iv30", "hv30", "spread30", "iv60", "hv60", "spread60"):
            disp[c] = (disp[c] * 100).round(2)
        st.dataframe(disp, use_container_width=True, hide_index=True)
        st.caption(
            "Spread = ATM IV − realized vol (vol pts). Positive/top = rich (premium-"
            "selling edge); negative/bottom = cheap (long-vol). 30d uses rv20, 60d rv60."
        )

    with st.expander("How to read this", expanded=False):
        st.markdown(
            "- **Surface / smile**: leftward tilt = put skew (downside premium); the "
            "front/next/far overlay shows skew flattening with tenor.\n"
            "- **Constant-maturity ATM**: 30/60/90d ATM IV, interpolated so it's "
            "comparable day to day even as expiries roll.\n"
            "- **Term structure + forward vol**: contango = calm/carry; backwardation "
            "= near-term stress.\n"
            "- **Sticky-strike changes**: how IV repriced at fixed strikes vs the prior "
            "snapshot (mechanical-vs-fear at the strike level).\n"
            "- **IV-HV screener**: rich names favor selling premium, cheap favor buying "
            "vol. Full framework: docs/guides/reading-the-vol-surface.md.\n"
            "- Descriptive regime views, not signals (FlashAlpha rule 4)."
        )


if __name__ == "__main__":
    main()
