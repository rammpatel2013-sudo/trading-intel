"""Vol Regime dashboard — Nations Indexes family + classifier read.

One-page surface for the ``strategies.vol_regime`` output: today's regime label,
the z-scores that drove it, recent transitions, and the underlying Nations
descriptors over time.

Reads:
- ``index_skew_daily`` — the Nations VOLI/TDEX/SDEX + CallDex/PutDex/RiskDex
  proxies, populated EOD by the ``index_skew`` job.
- ``signals`` — the ``INDEX_VOL_REGIME`` state + ``VOL_REGIME_TRANSITION``
  rows the ``vol_regime`` strategy emits each day.

This page is descriptor + state context. The trading bias for each label is
documented in ``docs/playbooks/vol_regime.md``; the live signal feeds the AM
report and (when wired) Discord.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings

st.set_page_config(page_title="Vol Regime", layout="wide")


# ── DB session helper ──────────────────────────────────────────────────


@st.cache_resource
def _session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return sessionmaker(bind=engine)()


# ── Data loaders ───────────────────────────────────────────────────────


@st.cache_data(ttl=300)
def _latest_signal() -> dict | None:
    sess = _session()
    row = sess.execute(
        text(
            """
            SELECT ts, payload, confidence
              FROM signals
             WHERE signal_type = 'INDEX_VOL_REGIME' AND symbol = 'INDEX'
             ORDER BY ts DESC
             LIMIT 1
            """
        )
    ).mappings().first()
    return dict(row) if row else None


@st.cache_data(ttl=300)
def _recent_transitions(limit: int = 10) -> pd.DataFrame:
    sess = _session()
    rows = sess.execute(
        text(
            """
            SELECT ts, payload, confidence
              FROM signals
             WHERE signal_type = 'VOL_REGIME_TRANSITION' AND symbol = 'INDEX'
             ORDER BY ts DESC
             LIMIT :n
            """
        ),
        {"n": limit},
    ).mappings().all()
    if not rows:
        return pd.DataFrame()
    out = []
    for r in rows:
        p = r["payload"] or {}
        out.append(
            {
                "date": pd.to_datetime(r["ts"]).date(),
                "from": p.get("prior_label"),
                "to": p.get("label"),
                "confidence": r["confidence"],
                "rationale": p.get("rationale"),
            }
        )
    return pd.DataFrame(out)


@st.cache_data(ttl=300)
def _history(lookback_days: int = 252) -> pd.DataFrame:
    sess = _session()
    rows = sess.execute(
        text(
            """
            SELECT date,
                   voli, tdex, sdex,
                   calldex_proxy, putdex_proxy, riskdex_proxy,
                   voli_pctile_252d, tdex_pctile_252d, sdex_pctile_252d
              FROM index_skew_daily
             ORDER BY date DESC
             LIMIT :n
            """
        ),
        {"n": lookback_days},
    ).mappings().all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows]).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ── Regime-card rendering ──────────────────────────────────────────────

_REGIME_DESCRIPTIONS = {
    "COMPLACENT": (
        "Sell premium — iron condors, short strangles. ATM cheap, tails cheap, "
        "flat skew. Normal size."
    ),
    "BUILDING_STRESS": (
        "Cut delta; long convexity. Tails + skew bid before ATM — smart-money "
        "hedging. Stop selling premium."
    ),
    "ACUTE_TAIL": (
        "Fade overpriced wings — put-credit spreads. All metrics rich. Avoid "
        "naked tail risk; half size."
    ),
    "VOL_CRUSH_SETUP": (
        "Long-dated short vol. IV peaked + RiskDex rolling over → mean-revert "
        "setup. Best risk/reward when it fires."
    ),
    "MIXED": "No edge from this signal. Defer to per-name strategies or stay flat.",
}

_REGIME_COLORS = {
    "COMPLACENT": "#10b981",
    "BUILDING_STRESS": "#f59e0b",
    "ACUTE_TAIL": "#ef4444",
    "VOL_CRUSH_SETUP": "#3b82f6",
    "MIXED": "#6b7280",
}


def _render_regime_card(sig: dict) -> None:
    payload = sig["payload"] or {}
    label = payload.get("label", "MIXED")
    confidence = sig.get("confidence")
    color = _REGIME_COLORS.get(label, "#6b7280")
    bias = _REGIME_DESCRIPTIONS.get(label, "—")

    conf_pct = f"{(confidence * 100):.0f}%" if confidence is not None else "—"

    st.markdown(
        f"""
        <div style="
            border-left: 8px solid {color};
            padding: 18px 22px;
            background: rgba(255,255,255,0.03);
            border-radius: 6px;
            margin-bottom: 12px;">
            <div style="font-size: 28px; font-weight: 700; color: {color};">
                {label}
            </div>
            <div style="font-size: 14px; color: #d1d5db; margin-top: 6px;">
                <b>Bias:</b> {bias}
            </div>
            <div style="font-size: 13px; color: #9ca3af; margin-top: 8px;">
                <b>Confidence:</b> {conf_pct} &nbsp;·&nbsp;
                <b>As of:</b> {pd.to_datetime(sig['ts']).date()} &nbsp;·&nbsp;
                <b>Rationale:</b> {payload.get('rationale', '—')}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_z_panel(payload: dict) -> None:
    z_voli = payload.get("z_voli")
    z_tdex = payload.get("z_tdex")
    z_sdex = payload.get("z_sdex")
    z_riskdex = payload.get("z_riskdex")
    momentum = payload.get("riskdex_5d_z_delta")

    cols = st.columns(5)
    for col, name, val, helptext in [
        (cols[0], "z(VOLI)", z_voli, "ATM IV. >+1 = rich; <-0.5 = cheap."),
        (cols[1], "z(TDEX)", z_tdex, "Tail cost. >+1.5 = capitulation hedging."),
        (cols[2], "z(SDEX)", z_sdex, "Skew premium. >+1 = pronounced put bid."),
        (cols[3], "z(RiskDex)", z_riskdex, "Put-vs-call cost ratio."),
        (cols[4], "5d Δz(RiskDex)", momentum, "Negative + high VOLI = vol crush setup."),
    ]:
        with col:
            display = "—" if val is None else f"{val:+.2f}"
            st.metric(name, display, help=helptext)


# ── Sparkline helper ───────────────────────────────────────────────────


def _line_chart(df: pd.DataFrame, columns: list[str], *, height: int = 220) -> None:
    if df.empty:
        st.info("No history yet.")
        return
    chart_df = df.set_index("date")[columns].dropna(how="all")
    if chart_df.empty:
        st.info("No history for these columns.")
        return
    st.line_chart(chart_df, height=height)


# ── Page ───────────────────────────────────────────────────────────────


def main() -> None:
    st.title("Vol Regime — Nations Indexes")
    st.caption(
        "Strategy: `strategies/vol_regime.py` · Playbook: `docs/playbooks/vol_regime.md` · "
        "Inputs populated by the EOD `index_skew` job."
    )

    sig = _latest_signal()
    if sig is None:
        st.warning(
            "No `INDEX_VOL_REGIME` signal yet. Run "
            "`python -m trading_intel.scheduler.jobs.vol_regime` "
            "(needs an `index_skew_daily` row)."
        )
        return

    _render_regime_card(sig)

    payload = sig["payload"] or {}
    if payload.get("experimental"):
        st.caption(
            ":warning: **Experimental** — rules not yet backtest-validated; "
            "use as a sizing input, not a trade gate."
        )

    st.subheader("Z-scores driving today's read")
    _render_z_panel(payload)

    # ── History block ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Nations descriptors (trailing 252d)")

    lookback = st.slider(
        "Lookback (trading days)",
        min_value=63, max_value=504, value=252, step=21,
        help="Backfill via scripts/backfill_index_skew.py to extend history.",
    )
    hist = _history(lookback_days=lookback)
    if hist.empty:
        st.info("No `index_skew_daily` history yet — run the backfill.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**VOLI** — Nations VolDex (ATM IV)")
            _line_chart(hist, ["voli"])
            st.markdown("**SDEX** — Nations SkewDex")
            _line_chart(hist, ["sdex"])
        with c2:
            st.markdown("**TDEX** — Nations TailDex")
            _line_chart(hist, ["tdex"])
            st.markdown("**RiskDex (proxy)** — PutDex / CallDex")
            _line_chart(hist, ["riskdex_proxy"])

        with st.expander("CallDex / PutDex proxies (vol points)"):
            _line_chart(hist, ["calldex_proxy", "putdex_proxy"])

        with st.expander("Latest row (full)"):
            st.dataframe(
                hist.tail(1).T.rename(columns={hist.tail(1).index[0]: "value"}),
                use_container_width=True,
            )

    # ── Transitions ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Recent regime transitions")
    trans = _recent_transitions(limit=15)
    if trans.empty:
        st.info(
            "No transitions yet — the classifier emits a transition row only "
            "when today's label differs from the prior day's."
        )
    else:
        st.dataframe(
            trans,
            column_config={
                "date": st.column_config.DateColumn("Date"),
                "from": st.column_config.TextColumn("From"),
                "to": st.column_config.TextColumn("To"),
                "confidence": st.column_config.NumberColumn(
                    "Conf.", format="%.2f",
                    help="max(|z|) / 3.0, capped at 1.0",
                ),
                "rationale": st.column_config.TextColumn("Rationale", width="large"),
            },
            hide_index=True,
            use_container_width=True,
        )

    st.caption(
        "Regime labels: `COMPLACENT` · `BUILDING_STRESS` · `ACUTE_TAIL` · "
        "`VOL_CRUSH_SETUP` · `MIXED`. See playbook for cutoffs."
    )


main()
