"""Surface + flow report orchestrator (dashboard-facing).

Assembles the inputs for the interpretive report: surface metrics (from the
latest stored oi_chain_eod snapshot), option flow (LIVE Convex if reachable,
else the stored flow_snapshots), and KB grounding, then generates the 3-part
narrative via the LLM (Ollama) with the deterministic build_surface_report as a
fallback. Descriptive regime read-through only - FlashAlpha rule 4.
"""
from __future__ import annotations

import structlog

from trading_intel.config import Settings
from trading_intel.dashboard.flow_data import load_latest_flow
from trading_intel.dashboard.vol_lab_data import latest_spx_chain
from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_delta_surface
from trading_intel.strategies.options_flow import FlowSummary, aggregate_flow, format_flow_markdown
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.surface_report import (
    build_surface_query,
    build_surface_report,
    interpret_surface_flow_llm,
    load_kb_context,
    surface_metrics,
)

log = structlog.get_logger(__name__)


def _flow_from_snapshot(snap) -> FlowSummary:
    """Reconstruct a FlowSummary from a stored flow_snapshots row."""
    return FlowSummary(
        call_notional=snap.call_notional or 0.0,
        put_notional=snap.put_notional or 0.0,
        net_premium=snap.net_premium,
        n_prints=snap.n_prints or 0,
        top_prints=snap.top_prints or [],
    )


def _live_flow(symbol: str, settings: Settings) -> FlowSummary | None:
    """Best-effort LIVE flow pull from Convex; None if unreachable / off-hours."""
    try:
        from trading_intel.clients.convex import ConvexClient

        chain = ConvexClient(settings).flow_chain(symbol)
        return aggregate_flow(chain)
    except Exception as exc:  # network / market closed / vendor error - degrade to stored
        log.warning("report.live_flow_failed", symbol=symbol, error=str(exc))
        return None


def generate_surface_flow_report(
    session,
    symbol: str,
    *,
    settings: Settings,
    llm: LLMProvider | None = None,
    prefer_live: bool = True,
) -> str:
    """Generate the surface + flow report for ``symbol`` (markdown)."""
    loaded = latest_spx_chain(session, symbol=symbol)
    if loaded is None:
        return f"_No stored oi_chain_eod snapshot for {symbol} yet — the report needs one._"
    chain, _spot, _ts = loaded
    try:
        metrics = surface_metrics(build_delta_surface(chain))
    except ComputationError as exc:
        return f"_Surface unavailable for {symbol}: {exc}_"

    flow = _live_flow(symbol, settings) if prefer_live else None
    flow_source = "live (Convex)" if flow is not None else None
    if flow is None:
        snap = load_latest_flow(session, symbol)
        if snap is not None:
            flow, flow_source = _flow_from_snapshot(snap), "stored snapshot"
    flow_md = format_flow_markdown(flow) if flow is not None else "(no flow data available)"

    try:
        kb = load_kb_context(session=session, llm=llm, query=build_surface_query(metrics))
    except Exception:  # grounding is best-effort
        kb = ""

    header = f"*{symbol} · flow source: {flow_source or 'none'}*\n\n"
    if llm is not None:
        try:
            return header + interpret_surface_flow_llm(metrics, flow_md, llm, kb_text=kb)
        except Exception as exc:  # Ollama down / model missing - deterministic fallback
            log.warning("report.llm_failed", error=str(exc))
            return (
                header
                + "*(LLM unavailable — deterministic fallback)*\n\n"
                + build_surface_report(metrics, flow=flow)
            )
    return header + build_surface_report(metrics, flow=flow)
