"""Tests for the post-earnings re-entry gate + emit path.

``evaluate_reentry`` is pure. ``emit_signals`` is exercised with a fake session so
no Postgres is needed (constructing the ``Signal`` ORM object needs only the
mapper, not a connection).
"""

from __future__ import annotations

from datetime import date

from trading_intel.strategies.em_break_reentry import (
    SIGNAL_TYPE,
    ReentryEval,
    emit_signals,
    evaluate_reentry,
)

_BULLISH = {
    "em_broke": True,
    "gamma_burned_off": True,
    "straddle_label": "decaying",
    "vrp_normalizing": True,
    "phase": "linear",
    "dealer_gamma_sign": 1.0,
    "spot": 95.0,
    "put_wall": 96.0,
    "call_wall": 110.0,
    "overwriter_rebuilding": True,
    "systematic_buying_usd": 1e9,
}


def test_full_setup_is_eligible_maxed():
    ev = evaluate_reentry(_BULLISH)
    assert isinstance(ev, ReentryEval)
    assert ev.prerequisites_met is True
    assert ev.eligible is True
    assert ev.conviction == 100.0  # every component contributes, capped
    assert ev.target == 110.0
    assert ev.stop_ref == 96.0
    assert ev.phase == "linear"


def test_prerequisites_gate_blocks_eligibility():
    feats = dict(_BULLISH, gamma_burned_off=False)
    ev = evaluate_reentry(feats)
    assert ev.prerequisites_met is False
    assert ev.eligible is False


def test_over_realizing_penalizes_but_may_stay_eligible():
    ev = evaluate_reentry(dict(_BULLISH, over_realizing=True))
    assert ev.conviction == 80.0  # 100 - 20 penalty
    assert ev.eligible is True


def test_repricing_up_and_missing_structure_falls_below_cutoff():
    feats = {
        "em_broke": True,
        "gamma_burned_off": True,
        "straddle_label": "repricing_up",  # vol not reset -> no vol-reset points
        "phase": "mechanical",
        "spot": 95.0,
        "put_wall": 80.0,  # spot well above support -> no structure points
    }
    ev = evaluate_reentry(feats)
    assert ev.eligible is False
    assert ev.conviction < 55.0


class _FakeResult:
    def __init__(self, val):
        self._val = val

    def scalar_one_or_none(self):
        return self._val


class _FakeSession:
    """Minimal stand-in: no dedupe hit, records added rows."""

    def __init__(self, existing=None):
        self._existing = existing
        self.added: list = []

    def execute(self, _stmt):
        return _FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def test_emit_signals_writes_only_eligible():
    session = _FakeSession(existing=None)
    feats = {"NFLX": _BULLISH, "AAPL": dict(_BULLISH, em_broke=False)}
    out = emit_signals(session, feats, as_of=date(2026, 7, 20))
    assert len(out) == 1
    sig = out[0]
    assert sig.symbol == "NFLX"
    assert sig.signal_type == SIGNAL_TYPE
    assert sig.payload["experimental"] is True
    assert sig.confidence == 1.0
    assert len(session.added) == 1


def test_emit_signals_idempotent_when_row_exists():
    session = _FakeSession(existing=1)  # a same-day row already present
    out = emit_signals(session, {"NFLX": _BULLISH}, as_of=date(2026, 7, 20))
    assert out == []
    assert session.added == []
