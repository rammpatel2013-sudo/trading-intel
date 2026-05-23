"""Daily AM regime report page.

Renders the markdown report produced by the ``am_summary`` job (one row per day
in ``am_summaries``): a market-wide regime line, the research-surfaced watchlist
with rationale, and the per-ticker regime tables. Leading ``0_`` sorts this to
the top of the dashboard nav.

Thin shell: all reads go through ``dashboard/am_report_data.py``. Descriptive
regime read-through only — not trade signals (FlashAlpha rule 4).
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import get_settings
from trading_intel.dashboard.am_report_data import (
    am_summary_by_date,
    available_dates,
    latest_am_summary,
)


def _session_factory() -> sessionmaker[Session]:
    factory = st.session_state.get("session_factory")
    if factory is None:
        from trading_intel.memory.db import make_session_factory

        factory = make_session_factory(get_settings())
        st.session_state["session_factory"] = factory
    return factory


def main() -> None:
    st.set_page_config(page_title="AM Report", page_icon="🌅", layout="wide")

    st.title("🌅 Daily AM report")
    st.caption(
        "Pre-market regime summary across the effective watchlist (static symbols "
        "plus research-surfaced tickers). Descriptive context, not trade signals "
        "(FlashAlpha rule)."
    )

    factory = _session_factory()
    try:
        with factory() as session:
            dates = available_dates(session)
            if not dates:
                st.info(
                    "No AM reports yet. Run the job to generate today's report:\n\n"
                    "`python -m trading_intel.scheduler.jobs.am_summary`"
                )
                return

            chosen = st.selectbox(
                "Report date",
                options=dates,
                index=0,
                format_func=lambda d: d.isoformat(),
            )
            report = (
                am_summary_by_date(session, chosen)
                if chosen is not None
                else latest_am_summary(session)
            )
    except SQLAlchemyError as exc:
        st.error(f"Could not load AM reports: {exc}")
        return

    if report is None:
        st.warning("No report found for the selected date.")
        return

    meta = report.metadata_json or {}
    model = report.claude_model or ("LLM" if meta.get("used_llm") else "deterministic fallback")
    st.caption(f"Generated for {report.date.isoformat()} · source: {model}")
    if meta.get("research_symbols"):
        st.caption("Research tickers: " + ", ".join(meta["research_symbols"]))

    st.markdown(report.markdown)


main()
