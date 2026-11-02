"""2026-10-29 (master list #80): one bad vendor print must not pin a z-score.

A Yahoo ^SDEX print of 184,467,424 on 2026-09-14 (median ~60) made the 252-day SD ~11.6M,
so z(SKEW) sat at -0.06 for six weeks whatever SDEX did.
"""
import numpy as np

from trading_intel.strategies.vol_regime import _z, drop_feed_glitches


def _hist():
    rng = np.random.default_rng(7)
    return list(60 + 4 * rng.standard_normal(251))


def test_glitch_is_dropped_and_z_moves_again():
    h = _hist()
    bad = h[:100] + [184_467_424.0] + h[100:]
    assert len(drop_feed_glitches(bad)) == len(h)
    z_hi, z_lo = _z(drop_feed_glitches(bad), 68.0), _z(drop_feed_glitches(bad), 52.0)
    assert z_hi > 1.5 and z_lo < -1.5          # with the glitch these were both about -0.06
    assert abs(_z(bad, 68.0) - _z(bad, 52.0)) < 1e-5


def test_real_spikes_are_kept():
    vix = [16.0] * 240 + [35.0, 45.0, 60.0, 80.0, 65.0, 40.0]   # 2020-style crash, 5x the median
    assert drop_feed_glitches(vix) == vix


def test_signed_or_short_series_untouched():
    term = [-2.0, -1.5, 0.3, -2.4, 1.1, -0.2]                     # VIX9D - VIX can be negative
    assert drop_feed_glitches(term) == term
    assert drop_feed_glitches([1.0, 2.0, float("nan")]) == [1.0, 2.0]
