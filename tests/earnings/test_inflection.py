"""Tests for the earnings-call inflection features — pure, deterministic."""

from __future__ import annotations

from trading_intel.earnings.inflection import (
    guidance_signal,
    read_inflection,
    score_tone,
)

POS_TEXT = (
    "We delivered strong record growth with robust demand and improved margins. "
    "Momentum accelerated and we are raising our guidance after we exceeded expectations. "
    "Confidence is high and results outperform."
)
NEG_TEXT = (
    "We faced significant headwinds and softness in demand this quarter. "
    "Results declined and we are lowering our guidance amid a challenging macro backdrop. "
    "Weakness and pressure led to a disappointing shortfall."
)
NEUTRAL_TEXT = (
    "During the quarter revenue and costs were in line with our plan. "
    "We continued to execute our strategy and returned capital to shareholders. "
    "Operations progressed as scheduled."
)


def test_score_tone_positive():
    st = score_tone(POS_TEXT)
    assert st.positive > 5
    assert st.negative == 0
    assert st.tone == 1.0


def test_score_tone_negative():
    st = score_tone(NEG_TEXT)
    assert st.negative > st.positive
    assert st.tone < -0.5


def test_score_tone_neutral_and_empty():
    assert score_tone("").tone == 0.0
    assert score_tone(NEUTRAL_TEXT).tone == 0.0


def test_uncertainty_density():
    st = score_tone("maybe this could be uncertain and volatile risk")
    assert st.uncertainty_density > 0.5  # maybe/could/uncertain/volatile/risk of 8 tokens


def test_guidance_signal_direction():
    assert guidance_signal("we are raising our guidance next year") == 1.0
    assert guidance_signal("we are lowering our guidance for the year") == -1.0
    assert guidance_signal("no forward-looking cues in this sentence") == 0.0


def test_positive_inflection_qoq():
    r = read_inflection("X", POS_TEXT, NEG_TEXT)  # good quarter after a bad one
    assert r.label == "positive inflection"
    assert r.tone_delta is not None and r.tone_delta > 0
    assert r.score > 0.15


def test_negative_inflection_qoq():
    r = read_inflection("X", NEG_TEXT, POS_TEXT)  # bad quarter after a good one
    assert r.label == "negative inflection"
    assert r.score < -0.15


def test_steady_when_unchanged():
    r = read_inflection("X", NEUTRAL_TEXT, NEUTRAL_TEXT)
    assert r.label == "steady / no clear inflection"
    assert -0.15 < r.score < 0.15


def test_no_prior_falls_back_to_absolute_tone():
    r = read_inflection("X", POS_TEXT)
    assert r.prior_tone is None and r.tone_delta is None
    assert r.label == "positive inflection"
