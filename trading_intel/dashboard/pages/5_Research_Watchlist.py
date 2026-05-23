"""Research-driven dynamic watchlist page.

Tickers surfaced from uploaded company-research documents (via the LLM ingest
pipeline), each with the rationale, sentiment and themes the model extracted —
cross-referenced with whatever regime metrics already exist for the symbol.

Thin shell: reads ``dashboard/dynamic_watchlist.py`` and reuses the watchlist
metrics. Descriptive context only — rationale/sentiment are read-throughs, not
trade signals (FlashAlpha rule 4).
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.dynamic_watchlist import (
    distinct_symbols,
    load_watchlist_entries,
)
from trading_intel.dashboard.watchlist_metrics import format_display, load_watchlist_metrics


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def main() -> None:
    st.set_page_config(page_title="Research Watchlist", page_icon="🧭", layout="wide")

    st.title("🧭 Research watchlist")
    st.caption(
        "Tickers surfaced from your uploaded company research, with the LLM's rationale "
        "and sentiment. Cross-referenced with collected regime metrics. Context, not signals "
        "(FlashAlpha rule)."
    )

    try:
        factory = _session_factory()
        with factory() as session:
            entries = load_watchlist_entries(session)
            symbols = distinct_symbols(entries)
            metrics = load_watchlist_metrics(session, symbols) if symbols else None
    except SQLAlchemyError as exc:
        st.error(f"Could not load research watchlist: {exc}")
        return

    if entries.empty:
        st.info(
            "No research ingested yet. Ingest a company-research PDF/docx with:\n\n"
            "`python -m trading_intel.memory.watchlist_ingest <path-to-file>`\n\n"
            "(Ollama must be running locally.)"
        )
        return

    st.subheader("Surfaced tickers")
    show = entries[["symbol", "sentiment", "confidence", "themes", "rationale"]].copy()
    show["sentiment"] = show["sentiment"].map(
        lambda v: f"{float(v):+.2f}" if v is not None and v == v else "n/a"
    )
    show["confidence"] = show["confidence"].map(
        lambda v: f"{float(v):.2f}" if v is not None and v == v else "n/a"
    )
    st.dataframe(
        show.rename(
            columns={
                "symbol": "Symbol", "sentiment": "Sentiment", "confidence": "Conf",
                "themes": "Themes", "rationale": "Rationale (per the doc)",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Regime metrics for surfaced tickers")
    if metrics is None or metrics.empty:
        st.info("No regime metrics yet for these symbols.")
    else:
        st.dataframe(format_display(metrics), use_container_width=True, hide_index=True)
        st.caption(
            "Metrics populate only for symbols the collectors track. To collect a newly "
            "surfaced name, add it to WATCHLIST in .env so the snapshot jobs pull it."
        )


main()
