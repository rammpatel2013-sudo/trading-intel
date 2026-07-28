"""Signal-alignment scoring (pure — no DB, no network)."""

from __future__ import annotations

from trading_intel.earnings.alignment import AlignmentInputs, score_alignment


def test_all_three_aligned_is_top_tier() -> None:
    r = score_alignment(AlignmentInputs(angle=0.8, flow=5e6, revision=0.03, confidence=0.9))
    assert r.aligned and r.tier_rank == 1 and r.bias == "bullish"


def test_angle_flow_aligned_without_revision_is_tier2() -> None:
    r = score_alignment(AlignmentInputs(angle=0.6, flow=3e6, revision=None))
    assert r.aligned and r.tier_rank == 2


def test_angle_and_revision_but_weak_flow_is_tier3() -> None:
    r = score_alignment(AlignmentInputs(angle=0.7, flow=0.0, revision=0.04))
    assert r.tier_rank == 3


def test_divergent_angle_and_flow_is_low_tier() -> None:
    r = score_alignment(AlignmentInputs(angle=0.7, flow=-4e6, revision=None))
    assert not r.aligned and r.tier_rank == 4


def test_bearish_alignment() -> None:
    r = score_alignment(AlignmentInputs(angle=-0.7, flow=-4e6, revision=-0.02))
    assert r.aligned and r.tier_rank == 1 and r.bias == "bearish"


def test_no_signal_is_bottom() -> None:
    r = score_alignment(AlignmentInputs(angle=None, flow=None, revision=None))
    assert r.tier_rank == 5 and r.bias == "mixed"


def test_stronger_setup_scores_higher() -> None:
    strong = score_alignment(AlignmentInputs(angle=0.9, flow=8e6, revision=0.05, confidence=1.0))
    weak = score_alignment(AlignmentInputs(angle=0.3, flow=1e6, revision=None))
    assert strong.score > weak.score
