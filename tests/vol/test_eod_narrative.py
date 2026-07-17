"""Tests for the deterministic EOD narrative engine (pure, no DB)."""

from __future__ import annotations

from trading_intel.vol.eod_narrative import (
    deltas,
    describe,
    dispersion_phrase,
    forward_bullets,
    pctile_phrase,
    term_phrase,
)


def test_deltas_basic() -> None:
    vals = [10.0, 11.0, 12.0, 13.0, 14.0, 19.44]  # 6 pts, week_lag default 5
    d = deltas(vals)
    assert d.latest == 19.44
    assert d.prev == 14.0
    assert round(d.dod, 2) == 5.44
    assert d.week_ago == 10.0
    assert round(d.wow, 2) == 9.44
    assert d.dod_pct is not None and d.dod_pct > 0


def test_deltas_handles_none_gaps() -> None:
    d = deltas([None, 12.0, None, 13.18])
    assert d.latest == 13.18
    assert d.prev == 12.0


def test_describe_sentence() -> None:
    s = describe("VIX", [22.22, 19.44], unit="", dp=2)
    assert "VIX 19.44" in s
    assert "down" in s and "d/d" in s


def test_pctile_phrase_extremes() -> None:
    assert "floor" in pctile_phrase(0.03)
    assert "top of its year" in pctile_phrase(0.95)


def test_term_phrase_inversion() -> None:
    s = term_phrase(20.66, 19.44, 21.42)
    assert "front inverted" in s
    assert "contango" in s


def test_dispersion_phrase_widening() -> None:
    s = dispersion_phrase(
        cor1m=13.18, cor3m=11.0, vixeq=45.25, vix=19.44,
        spread=25.81, spread_dod=2.65, cor1m_pctile=0.5,
    )
    assert "COR1M 13.18" in s
    assert "dispersion spread" in s
    assert "re-widening" in s


def test_forward_bullets_fires_rules() -> None:
    ctx = {
        "vix": 19.44, "vix9d": 20.66, "tail_pctile": 0.04,
        "cor1m": 13.18, "cor3m": 11.0, "spread_dod": 2.65,
        "vol_falling": True, "catalyst": ("June OPEX", 4),
    }
    bullets = forward_bullets(ctx)
    joined = " ".join(bullets)
    assert "crush fuel" in joined
    assert "protection" in joined
    assert "June OPEX" in joined
    assert len(bullets) >= 3


def test_forward_bullets_neutral_when_empty() -> None:
    assert forward_bullets({}) == ["No strong directional tells in the current vol state — neutral."]
