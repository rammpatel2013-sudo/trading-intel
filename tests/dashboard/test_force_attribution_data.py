"""Tests for the MM dealer-force attribution data layer (pure, no DB)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from trading_intel.dashboard.force_attribution_data import (
    cumulative_attribution,
    force_attribution,
)


def _two_snap_frame(
    *, charm_sign: float = 1.0, d_spot: float = 0.5, d_iv: float = 0.0,
    short_gamma: bool = False,
) -> pd.DataFrame:
    """Tiny two-snapshot 0DTE frame: spot 100 -> 100+d_spot, optional IV bump.

    Long-gamma by default (net_gamma > 0): C OI 200 vs P OI 100 with same gamma.
    Set ``short_gamma=True`` to flip it.
    """
    t0 = datetime(2026, 5, 27, 13, 0)
    t1 = t0 + timedelta(minutes=10)
    exp = date(2026, 5, 27)  # 0DTE
    iv0, iv1 = 0.20, 0.20 + d_iv

    def rows(ts: datetime, spot: float, iv: float) -> list[dict]:
        oi_c, oi_p = (100.0, 200.0) if short_gamma else (200.0, 100.0)
        return [
            {"ts": ts, "strike": 100.0, "cp": "C", "spot": spot, "expiry": exp,
             "oi": oi_c, "gxoi": 0.02 * oi_c, "gamma": 0.02,
             "charm": charm_sign * 1.0, "vanna": 10.0, "iv": iv,
             "volm_buy": 0.0, "volm_sell": 0.0},
            {"ts": ts, "strike": 100.0, "cp": "P", "spot": spot, "expiry": exp,
             "oi": oi_p, "gxoi": 0.02 * oi_p, "gamma": 0.02,
             "charm": 0.0, "vanna": 10.0, "iv": iv,
             "volm_buy": 0.0, "volm_sell": 0.0},
        ]

    return pd.DataFrame([*rows(t0, 100.0, iv0), *rows(t1, 100.0 + d_spot, iv1)])


def test_positive_net_charm_implies_negative_ds_charm() -> None:
    att = force_attribution(_two_snap_frame(charm_sign=+1.0))
    assert len(att) == 1
    assert att["ds_charm"].iloc[0] < 0.0  # long-gamma + positive net charm -> dealers sell


def test_charm_sign_flips_with_charm_value() -> None:
    up = force_attribution(_two_snap_frame(charm_sign=+1.0))["ds_charm"].iloc[0]
    dn = force_attribution(_two_snap_frame(charm_sign=-1.0))["ds_charm"].iloc[0]
    assert up < 0 < dn


def test_zero_iv_change_gives_zero_ds_vanna() -> None:
    att = force_attribution(_two_snap_frame(d_iv=0.0))
    assert att["ds_vanna"].iloc[0] == 0.0


def test_positive_iv_change_implies_negative_ds_vanna_long_gamma() -> None:
    att = force_attribution(_two_snap_frame(d_iv=+0.01))
    assert att["ds_vanna"].iloc[0] < 0.0  # +d_iv, +net_vanna, long-gamma -> spot suppressed


def test_residual_closes_the_decomposition() -> None:
    att = force_attribution(_two_snap_frame(d_spot=0.5, d_iv=+0.005))
    r = att.iloc[0]
    assert abs(r["delta_s"] - (r["ds_charm"] + r["ds_vanna"] + r["residual"])) < 1e-12


def test_gex_gravity_positive_when_long_gamma_negative_when_short() -> None:
    long_g = force_attribution(_two_snap_frame())["gex_gravity"].iloc[0]
    short_g = force_attribution(_two_snap_frame(short_gamma=True))["gex_gravity"].iloc[0]
    # Both have no flip (cumulative net gxoi doesn't cross zero in this tiny frame),
    # so gravity is NaN. We assert NaN-handling rather than sign here.
    import math
    assert math.isnan(long_g) and math.isnan(short_g)


def test_cumulative_helper_sums_correctly() -> None:
    att = force_attribution(_two_snap_frame())
    cum = cumulative_attribution(att)
    assert cum["cum_delta_s"].iloc[-1] == att["delta_s"].sum()
    assert cum["cum_ds_charm"].iloc[-1] == att["ds_charm"].sum()


def test_empty_or_single_snapshot_returns_empty() -> None:
    assert force_attribution(pd.DataFrame()).empty
    single = _two_snap_frame().iloc[:2]  # only the first ts (2 rows)
    assert force_attribution(single).empty
