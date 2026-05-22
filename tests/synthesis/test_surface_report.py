"""Tests for the vol-surface interpretation layer — pure, no Ollama/network."""

from __future__ import annotations

from datetime import date

import numpy as np

from trading_intel.greeks.surface import DeltaSurface
from trading_intel.synthesis.surface_report import (
    interpret_surface,
    interpret_surface_llm,
    surface_metrics,
)


def _surface() -> DeltaSurface:
    deltas = np.array([5, 7.5, 10, 15, 20, 25, 30, 35, 40, 45, 47.5, 50], dtype=float)
    # downside skew (puts richer at low delta), rising term structure
    iv_put = np.array([[0.13 + (50 - d) * 0.002 + j * 0.01 for d in deltas] for j in range(3)])
    iv_call = np.array([[0.13 + (50 - d) * 0.001 + j * 0.01 for d in deltas] for j in range(3)])
    return DeltaSurface(
        deltas=deltas,
        dte=np.array([30, 60, 90]),
        expiries=[date(2026, 5, 29), date(2026, 6, 18), date(2026, 7, 17)],
        iv_put=iv_put,
        iv_call=iv_call,
        spot=7365.12,
        ref=date(2026, 5, 21),
    )


class _FakeLLM:
    def complete(self, prompt, *, model=None, max_tokens=2048):
        assert "Metrics (JSON)" in prompt  # the surface-interp prompt was rendered
        return "  Surface shows downside skew and contango.  "

    def chat(self, messages, *, model=None, max_tokens=2048):
        return ""

    def embed(self, text, *, model=None):
        return [[0.0]]


def test_surface_metrics():
    m = surface_metrics(_surface())
    assert m["spot"] == 7365.12
    assert len(m["per_expiry"]) == 3
    front = m["per_expiry"][0]
    assert front["dte"] == 30
    assert front["skew_25d"] > 0  # downside skew (put > call at 25d)
    assert m["term_slope"] > 0  # rising term structure


def test_interpret_surface_deterministic():
    text = interpret_surface(surface_metrics(_surface()))
    assert "## Surface read" in text
    assert "skew" in text.lower()
    assert "Term structure" in text


def test_interpret_surface_llm_uses_prompt():
    out = interpret_surface_llm(surface_metrics(_surface()), _FakeLLM(), kb_text="notes")
    assert out == "Surface shows downside skew and contango."


# ── build_surface_report (composition) ───────────────────────────────────────

import pandas as pd  # noqa: E402

from trading_intel.strategies.options_flow import (  # noqa: E402
    aggregate_flow,
    detect_structures,
    flowsum_by_expiry,
)
from trading_intel.synthesis.surface_report import build_surface_report  # noqa: E402


def test_build_surface_report_surface_only():
    md = build_surface_report(surface_metrics(_surface()))
    assert "## Surface read" in md
    assert "## Flow (today)" not in md
    assert "## Notable packages" not in md


def test_build_surface_report_full():
    metrics = surface_metrics(_surface())
    flow = aggregate_flow(
        pd.DataFrame(
            [{"opt_kind": "put", "premium": 200e6}, {"opt_kind": "call", "premium": 100e6}]
        )
    )
    flowsum = flowsum_by_expiry(
        pd.DataFrame(
            [
                {"expiration": "2026-05-29", "opt_kind": "call", "volm_buy": 100,
                 "volm_sell": 60, "oi": 1000, "gxoi": 5.0},
                {"expiration": "2026-05-29", "opt_kind": "put", "volm_buy": 40,
                 "volm_sell": 90, "oi": 1500, "gxoi": -3.0},
            ]
        )
    )
    tas = pd.DataFrame(
        [
            {"time": pd.Timestamp("2026-05-29 00:54"), "root": "SPXW",
             "expiration": "2026-05-29", "strike": 7400.0, "opt_kind": "call",
             "size": 50, "premium": 371500.0, "aggressor_side": "sell"},
            {"time": pd.Timestamp("2026-05-29 00:54"), "root": "SPXW",
             "expiration": "2026-05-29", "strike": 7430.0, "opt_kind": "call",
             "size": 50, "premium": 247000.0, "aggressor_side": "sell"},
        ]
    )
    structures = detect_structures(tas)
    md = build_surface_report(metrics, flow=flow, flowsum=flowsum, structures=structures)
    assert "## Surface read" in md
    assert "## Flow (today)" in md
    assert "## Greek-OI by expiry (flowsum)" in md
    assert "## Notable packages" in md
    assert "call spread" in md
