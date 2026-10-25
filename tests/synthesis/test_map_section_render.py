"""The MAP block renderer — plain English, no odds, signed-flow row."""

from __future__ import annotations

from trading_intel.synthesis.daily_brief_render import _map_section, _pct0, _usd, _usd_signed


def _ctx() -> dict:
    return {
        "map_block": {
            "symbol": "SPX",
            "spot": 7622.9,
            "as_of": "2026-10-26T17:15:00",
            "chain_age_days": 0,
            "chain_stale": False,
            "regime": {
                "long_gamma": False,
                "label": "dealers short gamma",
                "net_gamma_dollars": -1.4e9,
                "between_switches": True,
            },
            "switches": [
                {"name": "whole-book flip", "value": 7736.0, "above": False},
                {"name": "same-day flip", "value": 7590.0, "above": True},
            ],
            "wall_views": [
                {"label": "This week", "note": "holds up overnight",
                 "put_wall": 7600.0, "call_wall": 7800.0, "stale": False},
                {"label": "Today only", "note": "written after 9:30",
                 "put_wall": 7640.0, "call_wall": 7750.0, "stale": True},
            ],
            "heaviest": 7650.0,
            "flow": {
                "found": True, "net": -120.0, "tilt": -0.12,
                "customers": "sold", "steadying": True,
                "at_levels": [{"strike": 7600.0, "net": -40.0, "customers": "sold"},
                              {"strike": 7800.0, "net": 12.0, "customers": "bought"}],
                "busiest": [{"strike": 7650.0, "net": -90.0, "customers": "sold"}],
            },
            "atm_iv": 0.139,
            "cones": [
                {"delta": 0.25, "band_pct": 50, "lower": 7560.0, "upper": 7690.0},
                {"delta": 0.15, "band_pct": 70, "lower": 7520.0, "upper": 7730.0},
                {"delta": 0.10, "band_pct": 80, "lower": 7490.0, "upper": 7760.0},
            ],
            "hedge": {"sell_on_drop": 2.1e9, "buy_on_rally": -1.1e9, "ratio": 1.9,
                      "sell_on_drop_iv": 2.9e9, "buy_on_rally_iv": -0.8e9,
                      "iv_up": 2.0, "iv_down": 2.0},
            "fragility": {
                "firm": False, "calibrating": False, "graded_sessions": 61,
                "max_move": 22.0, "tol": 13.0, "iv_bump": 1.0,
                "legs": [{"name": "walls", "firm": True, "move": 4.0},
                         {"name": "flip", "firm": False, "move": 22.0}],
                "soft_parts": ["flip"],
            },
            "n_contracts": 1482,
            "caveat": "These are positions, not predictions.",
        }
    }


def test_renders_the_whole_block():
    html = _map_section(_ctx())
    for needle in (
        "The map", "Dealers amplify moves here", "Price is below the 7,736 switch",
        "This week", "Today only", "7,600", "7,800",
        "Heaviest pile of all", "7,650",
        "Mostly sellers", "tends to steady price",
        "7,600 was sold", "7,800 was bought",
        "<b>50%</b> 7,560&ndash;7,690",
        "1% down &rarr; dealers sell about $2.10B",
        "Could shift", "slides the switch about 22 points",
        "These are positions, not predictions",
    ):
        assert needle in html, needle


def test_no_jargon_leaks_onto_the_page():
    """Mithil's rule: plain English on the brief, technicality via the MCP."""
    html = _map_section(_ctx())
    for banned in ("gxoi", "gamma x OI", "N(d2)", "Breeden", "net_gex",
                   "Δ", "gammas_buy", "gammas_sell", "DTE"):
        assert banned not in html, banned


def test_three_ways_block_is_gone():
    html = _map_section(_ctx())
    for gone in ("Three ways", "it closes", "below 7,600</span>", "between</span>"):
        assert gone not in html, gone


def test_long_gamma_flips_the_headline_and_colour():
    ctx = _ctx()
    ctx["map_block"]["regime"]["long_gamma"] = True
    ctx["map_block"]["switches"][0]["above"] = True
    html = _map_section(ctx)
    assert "Dealers cushion moves here" in html
    assert "gm-long" in html and "\U0001F7E2" in html
    assert "Price is above the 7,736 switch" in html


def test_flow_row_when_customers_are_buying():
    ctx = _ctx()
    ctx["map_block"]["flow"].update({"net": 90.0, "customers": "bought", "steadying": False})
    html = _map_section(ctx)
    assert "Mostly buyers" in html
    assert "tends to loosen price" in html


def test_flow_row_before_any_data_exists():
    """Every session banked before 2026-10-26 has no buy/sell detail."""
    ctx = _ctx()
    ctx["map_block"]["flow"] = {"found": False}
    html = _map_section(ctx)
    assert "Not yet" in html
    assert "starts from the 2026-10-26 session" in html
    assert "Mostly" not in html


def test_calibrating_note_appears_while_learning():
    ctx = _ctx()
    ctx["map_block"]["fragility"].update({"calibrating": True, "graded_sessions": 7})
    assert "Still learning what counts as a lot — 7 days in" in _map_section(ctx)


def test_absent_block_renders_nothing():
    assert _map_section({}) == ""
    assert _map_section({"map_block": None}) == ""


def test_degraded_block_renders_without_leaking_none():
    ctx = {
        "map_block": {
            "symbol": "SPX", "spot": 7600.0, "as_of": None,
            "regime": {"long_gamma": True}, "switches": [],
            "wall_views": [{"label": "This week", "note": "", "put_wall": None,
                            "call_wall": None, "stale": False}],
            "heaviest": None, "flow": {"found": False}, "atm_iv": None,
            "cones": [{"delta": 0.25, "band_pct": 50, "lower": None, "upper": None}],
            "hedge": {}, "fragility": {"firm": None, "legs": []},
            "n_contracts": 0, "caveat": "",
        }
    }
    html = _map_section(ctx)
    assert "None" not in html
    assert "No switch level available today" in html
    assert "not available" in html
    assert "Can't tell" in html


def test_stale_chain_is_flagged_in_plain_words():
    ctx = _ctx()
    ctx["map_block"]["chain_stale"] = True
    ctx["map_block"]["chain_age_days"] = 3
    assert "3 day(s) old" in _map_section(ctx)


def test_formatters():
    assert _pct0(0.126) == "13%"
    assert _pct0(-0.12) == "12%"
    assert _pct0(None) == "—"
    assert _usd(2.1e9) == "$2.10B"
    assert _usd(8e8) == "$800M"
    assert _usd(None) == "—"
    assert _usd_signed(-1.1e9) == "−$1.10B"
