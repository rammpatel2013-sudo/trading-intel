"""The Monday / OpEx-day roll-off block."""

from __future__ import annotations

from datetime import date

from trading_intel.market.rolloff import build_rolloff
from trading_intel.synthesis.daily_brief_render import _rolloff_html

MON = date(2026, 10, 26)


def _ctx(term, today=MON):
    return {"rolloff": build_rolloff(term, today=today)}


def _term():
    return [
        {"expiration": "2026-10-26", "dte": 0, "gex": 400.0},
        {"expiration": "2026-10-28", "dte": 2, "gex": 200.0},
        {"expiration": "2026-10-30", "dte": 4, "gex": 300.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": 100.0},
    ]


def test_absent_rolloff_renders_nothing():
    assert _rolloff_html({}) == ""
    assert _rolloff_html({"rolloff": {"found": False, "steps": []}}) == ""
    assert _rolloff_html(_ctx([])) == ""


def test_renders_every_expiry_and_the_step_down():
    html = _rolloff_html(_ctx(_term()))
    assert "Gamma roll-off" in html
    assert "week to 2026-10-30" in html
    for exp in ("2026-10-26", "2026-10-28", "2026-10-30"):
        assert exp in html
    assert "2026-11-20" not in html.split("<tbody>")[1].split("</tbody>")[0]
    assert "90%" in html  # week share
    assert "today" in html  # dte 0 label


def test_names_the_biggest_expiry():
    html = _rolloff_html(_ctx(_term()))
    assert "Most of it sits on" in html
    assert "40% of the whole book" in html


def test_warns_when_the_big_pole_is_not_this_week():
    term = [
        {"expiration": "2026-10-28", "dte": 2, "gex": 100.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": 900.0},
    ]
    html = _rolloff_html(_ctx(term))
    assert "NOT this week" in html


def test_opex_day_gets_its_own_callout():
    opex = date(2026, 10, 16)
    term = [
        {"expiration": "2026-10-16", "dte": 0, "gex": 800.0},
        {"expiration": "2026-11-20", "dte": 35, "gex": 200.0},
    ]
    html = _rolloff_html(_ctx(term, today=opex))
    assert "Monthly OpEx" in html
    assert "80%" in html
    assert "settles on this morning's prints" in html


def test_no_opex_callout_on_an_ordinary_monday():
    assert "Monthly OpEx" not in _rolloff_html(_ctx(_term()))


def test_flags_an_expiry_that_flips_the_net_sign():
    """A long book that goes short once the big call expiry settles."""
    term = [
        {"expiration": "2026-10-28", "dte": 2, "gex": 900.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": -400.0},
    ]
    html = _rolloff_html(_ctx(term))
    assert "flips the other way" in html


def test_negative_remaining_shows_its_sign():
    """A −$1.1B book must not read as $1.1B just because the cell is red."""
    term = [
        {"expiration": "2026-10-28", "dte": 2, "gex": 900.0},
        {"expiration": "2026-11-20", "dte": 25, "gex": -400.0},
    ]
    html = _rolloff_html(_ctx(term))
    assert "−$400" in html
    assert "arw-dn" in html


def test_usd_signed_helper():
    from trading_intel.synthesis.daily_brief_render import _usd, _usd_signed

    assert _usd_signed(-1.1e9) == "−$1.10B"
    assert _usd_signed(1.1e9) == "$1.10B"
    assert _usd_signed(None) == "—"
    assert _usd(-1.1e9) == "$1.10B"  # unsigned on purpose — words carry direction
