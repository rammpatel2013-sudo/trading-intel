"""Options-flow page — major flow across the watchlist.

A watchlist-wide overview of today's options flow (call/put premium notional,
put/call tilt, net premium) from the stored ``flow_snapshots``, plus a per-symbol
drill-down into the largest prints and notable multi-leg packages.

Thin shell: reads via ``dashboard/flow_data.py``; flow is aggregated by the
descriptive ``strategies/options_flow.py`` (FlashAlpha rule 4 — no signals).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.flow_data import load_latest_flow, load_watchlist_flow
from trading_intel.watchlist import effective_symbols

_OVERVIEW_LABELS = {
    "symbol": "Symbol",
    "ts": "As of",
    "put_call_ratio": "P/C",
    "tilt": "Tilt",
    "call_notional": "Call $",
    "put_notional": "Put $",
    "net_premium": "Net prem",
    "n_prints": "Prints",
}


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def _money(v: object) -> str:
    if not pd.notna(v):
        return "n/a"
    val = float(v)
    if abs(val) >= 1e6:
        return f"${val / 1e6:,.1f}M"
    return f"${val / 1e3:,.0f}K"


def _format_overview(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ts"] = out["ts"].map(
        lambda t: pd.Timestamp(t).strftime("%m-%d %H:%M") if pd.notna(t) else "n/a"
    )
    for col in ("call_notional", "put_notional", "net_premium"):
        out[col] = out[col].map(_money)
    out["put_call_ratio"] = out["put_call_ratio"].map(
        lambda v: f"{float(v):.2f}" if pd.notna(v) else "n/a"
    )
    out["n_prints"] = out["n_prints"].map(lambda v: int(v) if pd.notna(v) else 0)
    return out[list(_OVERVIEW_LABELS)].rename(columns=_OVERVIEW_LABELS)


def main() -> None:
    st.set_page_config(page_title="Flow", page_icon="💸", layout="wide")
    settings = get_settings()

    st.title("💸 Options flow")
    st.caption(
        "Major options flow across the watchlist — call/put premium notional, tilt, and "
        "notable packages. Descriptive read-through, not signals (FlashAlpha rule)."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            symbols = effective_symbols(session, settings)
            if not symbols:
                st.warning("No symbols configured in the watchlist.")
                return
            overview = load_watchlist_flow(session, symbols)
            selected = st.sidebar.selectbox("Drill into", symbols, index=0)
            detail = load_latest_flow(session, selected) if selected else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load flow: {exc}")
        return

    st.subheader("Watchlist flow overview")
    if overview.empty:
        st.info(
            "No flow stored yet. The flow collector populates this every 30 min during "
            "the session (09:30-16:00 ET) once it is running on the NAS."
        )
    else:
        st.dataframe(_format_overview(overview), use_container_width=True, hide_index=True)

    st.subheader(f"{selected} — largest prints & packages")
    if detail is None:
        st.info(f"No flow stored for {selected} yet.")
        return

    pcr = f"{detail.put_call_ratio:.2f}" if detail.put_call_ratio is not None else "n/a"
    st.caption(
        f"As of {pd.Timestamp(detail.ts):%Y-%m-%d %H:%M} · tilt: {detail.tilt} · "
        f"P/C {pcr} · call {_money(detail.call_notional)} / put {_money(detail.put_notional)}"
    )

    prints = pd.DataFrame(detail.top_prints or [])
    if not prints.empty:
        prints = prints.copy()
        prints["premium"] = prints["premium"].map(_money)
        st.markdown("**Largest prints**")
        st.dataframe(prints, use_container_width=True, hide_index=True)
    else:
        st.markdown("_No prints recorded in this snapshot._")

    packages = detail.packages or []
    st.markdown("**Notable multi-leg packages**")
    if not packages:
        st.markdown("_No multi-leg packages detected in the sampled prints._")
    else:
        for pkg in packages:
            exps = "/".join(pkg.get("expirations", []))
            st.markdown(
                f"- **{pkg.get('root')}** {exps} {pkg.get('kind')} "
                f"({pkg.get('n_legs')} legs): {_money(pkg.get('total_premium'))} total, "
                f"{_money(pkg.get('net_premium'))} net"
            )


main()
