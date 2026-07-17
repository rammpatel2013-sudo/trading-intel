"""Importable entry point for the full single-ticker HTML report.

The report LAYOUT is defined once in ``scripts/ticker_report.py`` (the canonical
generator + CLI — see MEMORY ``ticker-report``). This module loads that script's
``build()`` so other code (the MCP server's ``generate_ticker_report`` tool) can
produce the exact same report on demand without duplicating the layout. Keeping
a single source means refinements to the report only ever touch the one script.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ticker_report.py"
_EOD_VOL_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eod_vol_report.py"
_FLOW_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "flow_report.py"
_VOL_SURFACE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vol_surface_report.py"


def _load(path: Path, modname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load report generator at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build(symbol: str, *, days: int = 180) -> str:
    """Generate the ticker report for ``symbol`` and return the written HTML path."""
    return str(_load(_SCRIPT, "_ticker_report_impl").build(symbol, days=days))


def build_eod_vol(*, days: int = 252, llm: object = None, settings: object = None) -> str:
    """Generate the EOD volatility report and return the written HTML path.

    Single source of truth is ``scripts/eod_vol_report.py`` (the CLI), so the
    MCP ``generate_eod_vol_report`` tool produces the identical report. When an
    ``llm`` (``LLMProvider``) is passed, each tab gets a knowledge-grounded note
    (local Ollama; degrades silently if unavailable).
    """
    return str(
        _load(_EOD_VOL_SCRIPT, "_eod_vol_report_impl").build(days=days, llm=llm, settings=settings)
    )


def build_flow(
    *,
    lookback_days: int = 21,
    recent_days: int = 5,
    min_notional: float = 1_000_000.0,
    llm: object = None,
    settings: object = None,
) -> str:
    """Generate the EOD option-tape Flow Report and return the written HTML path.

    Single source of truth is ``scripts/flow_report.py`` (the CLI), so the MCP
    ``generate_flow_report`` tool produces the identical report. Reads the durable
    ``tas_daily_flow`` / ``tas_daily_contract`` roll-up tables. An optional ``llm``
    (``LLMProvider``, local Ollama) adds a narrative; degrades silently.
    """
    return str(
        _load(_FLOW_SCRIPT, "_flow_report_impl").build(
            lookback_days=lookback_days,
            recent_days=recent_days,
            min_notional=min_notional,
            llm=llm,
            settings=settings,
        )
    )


def build_vol_surface(symbol: str) -> str:
    """Generate the vol-surface-changes report for ``symbol`` and return the HTML path.

    Single source is ``scripts/vol_surface_report.py`` (the CLI), so the MCP
    ``generate_vol_surface_report`` tool produces the identical report: the delta-moneyness
    × expiry surface + day-over-day changes + the multi-day fixed-delta vol *footprint* read
    (long/short-gamma inference cross-checked against GEX). Reads banked ``surface_snapshots``.
    """
    return str(_load(_VOL_SURFACE_SCRIPT, "_vol_surface_report_impl").build(symbol))
