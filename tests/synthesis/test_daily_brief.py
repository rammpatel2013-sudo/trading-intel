"""Render tests for the daily brief (pure renderer — no DB, no network)."""

from __future__ import annotations

from trading_intel.synthesis.daily_brief_render import (
    _direction,
    _spark_points,
    render_html,
)


def _ctx() -> dict:
    return {
        "as_of": "2026-07-28",
        "subtitle": "pre-open daily brief",
        "through_line": "SPX, QQQ sit below their gamma flip.",
        "indices": [
            {
                "symbol": "SPY",
                "spot": 745.5,
                "flip": 739.1,
                "spot_vs_flip_pct": 0.87,
                "regime": "long gamma (> flip, move-damping)",
                "flip_series": [749.2, 743.3, 739.1],
                "gex_series": [-5.1, 6.6, 10.3],
                "asof": "2026-07-27",
            }
        ],
        "vix": {"vix": 18.58, "vvix": 100.9, "vix9d": 18.1, "vix3m": 20.2, "term": -2.07,
                "vrp": 8.4, "vega_zone": "low", "floor": 16, "call_wall": 25, "call_oi_share": 0.59},
        "doc": {"flip": 7458, "spot": 7413, "call_wall": 7600, "put_wall": 7400,
                "em_lo": 7356, "em_hi": 7471, "r16_lo": 7326, "r16_hi": 7500,
                "walls_stale": True, "expectation": "Spot below flip.", "expectation_src": "reconstructed"},
        "em_levels": {"current_spot": 7413, "current_src": "SPY×10", "as_of": "2026-07-28", "rows": [
            {"tenor": "Quarterly", "iv_label": "VIX3M", "anchor_date": "2026-07-01", "anchor_spot": 7420,
             "em_pct": 10.1, "upper": 8170, "lower": 6670, "pos_pct": 49.5, "status": "mid-range (balanced)"},
            {"tenor": "Weekly", "iv_label": "VIX9D", "anchor_date": "2026-07-27", "anchor_spot": 7455,
             "em_pct": 1.30, "upper": 7552, "lower": 7358, "pos_pct": 28.4, "status": "near lower rail"},
        ]},
        "recap": {"recap": "SPY above its flip; VIX 18.6.", "outlook": "Sell front vol into FOMC.",
                  "outlook_src": "Doc letter 2026-07-28"},
        "mag7": [
            {"symbol": "TSLA", "spot": 313.0, "flip": 330.0, "vs_flip": -5.2, "gex": -500,
             "regime": "short gamma (< flip, move-amplifying)", "atm_iv": 0.94, "found": True},
            {"symbol": "AAPL", "spot": 312.85, "flip": 310.0, "vs_flip": 0.9, "gex": 6760,
             "regime": "long gamma (> flip, move-damping)", "atm_iv": 0.333, "found": True},
        ],
        "flows": [
            {"root": "NVDA", "notional": 45e6, "net_delta": 12e6, "label": "accumulation", "score": 62},
        ],
        "letters": [{"src": "Doc McGraw", "text": "Respect the flip."}],
        "tracker": [{"src": "letters", "ticker": "CRM", "dir": "Bear", "note": "Fading Moat", "status": "surfaced"}],
        "learned": [{"symbol": "TSM", "themes": ["chip makers"], "sentiment": -0.5, "rationale": "memory drop"}],
        "learned_total": 168,
        "crosschecks": [{"claim": "short gamma", "source": "Doc", "our": "SPX below flip", "verdict": "✅", "cls": "ok"}],
    }


def test_render_produces_full_html() -> None:
    html = render_html(_ctx())
    assert "<html" in html and "</html>" in html
    assert "Trading-Intel Daily" in html
    assert "SPY" in html and "Zero-γ" in html
    assert "ladder" in html  # Doc level ladder SVG present
    assert "Expected-move rails" in html  # anchored EM rails section
    assert "near lower rail" in html or "mid-range" in html  # position read
    assert "Mag7" in html and "TSLA" in html  # Mag7 index-driver panel
    assert "Top option flow" in html and "Yesterday:" in html  # flows + recap
    assert html.count("<polyline") >= 1  # sparklines rendered


def test_sparkline_and_direction() -> None:
    assert _spark_points([1.0, 2.0, 3.0]) != ""
    assert _spark_points([None]) == ""
    assert _spark_points([5.0]) == ""  # single point -> no line
    assert _direction([749.0, 745.0, 739.0])[0] == "▼"
    assert _direction([680.0, 690.0, 700.0])[0] == "▲"
    assert _direction([None, None])[1] == "flat"


def test_render_handles_empty_sections() -> None:
    html = render_html(
        {
            "as_of": "2026-01-01",
            "indices": [],
            "vix": {},
            "doc": {},
            "letters": [],
            "tracker": [],
            "learned": [],
            "crosschecks": [],
        }
    )
    assert "<html" in html and "</html>" in html
    assert "Trading-Intel Daily" in html
