"""Tests for the EOD flow report rendering (``scripts/flow_report.py``).

The script is loaded by path (mirrors ``reports.py`` / how the MCP tool runs it),
and its pure functions — ``key_findings``, ``render_html``, ``_money`` — are
exercised on a synthetic report dict. No DB / no Convex.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "flow_report.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("flow_report_impl_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rep() -> dict[str, Any]:
    return {
        "as_of": "2026-07-12",
        "lookback_days": 21,
        "recent_days": 5,
        "count": {"trend": 2, "contracts": 1},
        "trend": [
            {
                "root": "AAA",
                "days_observed": 10,
                "recent_score": 100.0,
                "prior_score": 0.0,
                "score_delta": 100.0,
                "net_dollar_delta": 5_000_000.0,
                "streak_days": 5,
                "label": "accumulation",
            },
            {
                "root": "BBB",
                "days_observed": 10,
                "recent_score": -100.0,
                "prior_score": 0.0,
                "score_delta": -100.0,
                "net_dollar_delta": -4_000_000.0,
                "streak_days": -5,
                "label": "distribution",
            },
        ],
        "contracts": [
            {
                "root": "AAA",
                "expiry": "2026-09-18",
                "strike": 250.0,
                "cp": "C",
                "days_seen": 6,
                "total_notional": 3_000_000.0,
                "cum_net_dollar_delta": 2_000_000.0,
                "build_side": "accumulation",
            }
        ],
        "new": ["CCC"],
        "fading": ["DDD"],
    }


def test_key_findings_mentions_leaders() -> None:
    finds = _load().key_findings(_rep())
    text = " ".join(finds)
    assert "AAA" in text  # strongest accumulation
    assert "CCC" in text  # newly on board
    assert "DDD" in text  # fading


def test_render_html_structure() -> None:
    html = _load().render_html(_rep())
    assert "<!doctype html>" in html.lower()
    assert "EOD Flow Report" in html
    assert "AAA" in html and "BBB" in html
    assert "<table" in html
    assert "$5.0M" in html  # net-delta money formatting


def test_money_formatter() -> None:
    money = _load()._money
    assert money(1_500_000_000) == "$1.50B"
    assert money(-45_000_000) == "-$45.0M"
    assert money(None) == "—"
