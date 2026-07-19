"""Tests for the em_break_reentry job's enrichment-hook mappers (pure, no I/O)."""

from __future__ import annotations

from trading_intel.flows import CallStrikeChange
from trading_intel.scheduler.jobs.em_break_reentry import (
    _overwriter_rebuilding,
    _straddle_label,
    _vrp_normalizing,
)


def test_straddle_label_maps_decay():
    assert _straddle_label({"found": True, "decay": {"label": "decaying"}}) == "decaying"
    assert _straddle_label({"found": True, "decay": {"label": "repricing_up"}}) == "repricing_up"
    assert _straddle_label({"found": True, "decay": {"label": "flat"}}) == "flat"
    assert _straddle_label({"found": True}) is None  # no prior snapshot to diff
    assert _straddle_label({"found": False}) is None


def test_vrp_normalizing_percentile_scale_robust():
    assert _vrp_normalizing({"vrp_pctile": 0.2}) is True  # bottom half (0-1 scale)
    assert _vrp_normalizing({"vrp_pctile": 0.8}) is False  # still rich
    assert _vrp_normalizing({"vrp_pctile": 20}) is True  # 0-100 scale handled
    assert _vrp_normalizing({"vrp_pctile": 80}) is False


def test_vrp_normalizing_label_fallback():
    assert _vrp_normalizing({"label": "cheap"}) is True
    assert _vrp_normalizing({"label": "rich"}) is False
    assert _vrp_normalizing(None) is None
    assert _vrp_normalizing({}) is None


def test_overwriter_rebuilding_supply_vs_demand():
    supply = [CallStrikeChange(strike=120, d_oi=8000, d_iv=-0.01, gxoi=2e6)]
    assert _overwriter_rebuilding(supply) is True
    demand = [CallStrikeChange(strike=120, d_oi=8000, d_iv=0.02)]  # OI up + IV up = buying
    assert _overwriter_rebuilding(demand) is False
    assert _overwriter_rebuilding([]) is None
