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

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ticker_report.py"


def build(symbol: str, *, days: int = 180) -> str:
    """Generate the report for ``symbol`` and return the written HTML path."""
    spec = importlib.util.spec_from_file_location("_ticker_report_impl", _SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load ticker report generator at {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(mod.build(symbol, days=days))
