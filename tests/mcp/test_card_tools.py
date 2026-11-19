"""card_tools (2026-11-14): the two MCP card tools register and the page renders without scripts."""
from __future__ import annotations

import json
from pathlib import Path

from trading_intel.mcp import card_tools


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def test_register_adds_both_tools():
    m = _FakeMCP()
    card_tools.register(m, None, None)
    assert {"get_odte_card", "generate_odte_card"} <= set(m.tools)


def _card() -> dict:
    return {
        "symbol": "SPX", "found": True, "session": "2026-11-13", "book_day": "2026-11-13", "book_kind": "am",
        "label": "Fri 11/13 08:35 book", "book_time": "2026-11-13T08:35",
        "centre": 7645.0, "forward": 7645.0, "prior_close": 7592.06, "prior_close_ts": "2026-11-12T18:10",
        "latest": {"spot": 7560.97, "ts": "2026-11-13T18:10", "after_book": True},
        "regime": {"key": "pos", "line": "POSITIVE GAMMA · ABOVE BOTH SWITCHES · MOVES CALM"},
        "switch_0dte": 7601.9, "board_switch": 7633.5, "board_switch_after_open": None, "am_settled_today": False,
        "gamma": {"at_centre": 11.3, "down_1pct": -55.7, "up_1pct": 75.3},
        "dealer_sign": "naive: dealers long the calls, short the puts",
        "levels": {"cap": {"strike": 7700.0, "call_oi": 48507, "put_oi": 28527},
                   "floor": {"strike": 7600.0, "call_oi": 26118, "put_oi": 39962},
                   "steps": [{"strike": 7650.0, "call_oi": 20000, "put_oi": 24137}],
                   "air_pockets": [], "biggest_total_oi": 77034},
        "straddle": 37.7, "straddle_strike": 7645.0, "atm_iv": 0.2125, "one_sigma": [7597.6, 7692.1],
        "ranges": {"50%": [7621.1, 7676.8], "70%": [7601.1, 7689.9], "80%": [7584.5, 7699.2]},
        "three_ways": {"edges": [7600.0, 7700.0], "break": 14.6, "range": 75.8, "pop": 9.7},
        "odds_quality": "smile", "odds_reasons": [],
        "overnight": {"gap_pts": 52.8, "prev_straddle": 46.3, "gap_x_prev_move": 1.14,
                      "vol": {"7d": {"now": 14.34, "prev": 15.55, "change": -1.21, "pct_rank": 60, "n": 5,
                                     "flag": "normal"},
                              "30d": {"now": 13.5, "prev": 14.57, "change": -1.08, "pct_rank": 60, "n": 5,
                                      "flag": "normal"}},
                      "oi": {"changed_pct": 41.7, "updated": True,
                             "top": [{"strike": 7600.0, "side": "puts", "change": 5092}]},
                      "vol_since_close": {"close": 14.04, "now": 13.5, "change": -0.54, "source": "x"},
                      "prev_book_day": "2026-11-12"},
        "if_then": [{"icon": "up", "if": "Near 7,700", "then": "Cap."},
                    {"icon": "down", "if": "Loses 7,600", "then": "Below the floor."}],
        "checks": {"one_centre": True, "forward_spread_pts": 0.25, "oi_updated": True, "front_is_today": True,
                   "smile_points": 114},
        "sources": {"forward": "parity", "levels": "oi_chain_eod"},
    }


def test_page_has_every_number_and_no_scripts():
    page = card_tools._render(_card())
    assert "<script" not in page.lower()
    for s in ("7,592", "7,645", "7,561", "7,602", "7,634", "7,700", "7,600", "76%", "+53 pts", "Overnight change",
              "updated overnight", "Where each number comes from", "<svg", "08:35 FORWARD"):
        assert s in page, s
    assert "Checks passed" in page


def test_page_shows_a_failed_check():
    c = _card()
    c["checks"]["oi_updated"] = False
    c["checks"]["front_is_today"] = False
    c["odds_reasons"] = ["hole: 7,560-7,570 has 0.0% vs ~4.1% for a smooth curve"]
    c["overnight"]["vol"]["7d"]["flag"] = "spike"
    page = card_tools._render(c)
    assert "Check:" in page and "hole: 7,560" in page and "did not change overnight" in page
    assert "VOL SPIKE" in page and "08:35 pull" in page
    json.dumps(c)                                   # card data stays JSON-safe for the MCP tool
    assert Path(card_tools.REPO / "scripts" / "odte_card_report.py").exists()
