"""Tape descriptors — hand-checked values."""

from __future__ import annotations

from trading_intel.market import tape as t


def _bars(closes, vols=None):
    vols = vols or [100.0] * len(closes)
    return [t.Bar(c + 1, c - 1, c, v) for c, v in zip(closes, vols)]


def test_obv_and_vpt_direction() -> None:
    b = _bars([10, 11, 10, 12], [100, 200, 50, 300])
    assert t.obv(b) == [0, 200, 150, 450]
    assert round(t.vpt(b)[-1], 3) == round(200 * 0.1 - 50 * (1 / 11) + 300 * 0.2, 3)


def test_cmf_close_at_high_is_positive_one() -> None:
    bars = [t.Bar(11, 9, 11, 100) for _ in range(20)]
    assert t.cmf(bars) == 1.0


def test_rel_volume() -> None:
    b = _bars([10] * 21, [100] * 20 + [250])
    assert t.rel_volume(b) == 2.5
    assert t.rel_volume(b[:10]) is None


def test_volume_profile_poc_in_heavy_zone() -> None:
    bars = [t.Bar(11, 9, 10, 1000) for _ in range(10)] + [t.Bar(21, 19, 20, 10)]
    vp = t.volume_profile(bars, bins=12)
    assert 9 <= vp["poc"] <= 11
    assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_anchored_vwap() -> None:
    bars = [t.Bar(12, 8, 10, 100), t.Bar(22, 18, 20, 300)]
    assert t.anchored_vwap(bars) == 17.5
