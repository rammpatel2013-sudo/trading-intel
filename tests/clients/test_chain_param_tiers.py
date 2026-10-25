"""The chain pull's tiered param fallback.

One rejected param 400s the whole Convex request, so widening the pull must
never be able to cost us a field that works today.
"""

from __future__ import annotations

import pandas as pd
import pytest

from trading_intel.clients import convex as cx
from trading_intel.errors import DataSourceError


class _Client(cx.ConvexClient):
    """ConvexClient with the vendor swapped for a recorder (no __init__ call)."""

    def __init__(self, accept) -> None:  # noqa: D107 - test double
        self._accept = accept
        self.asked: list[tuple[str, ...]] = []

    def _fetch_chain(self, symbol, params, *, exps, strike_range):
        self.asked.append(tuple(params))
        if not self._accept(params):
            raise DataSourceError(f"400 rejected: {params}")
        cols = {p: [1.0] for p in params}
        df = pd.DataFrame({"symbol": [symbol], "expiration": [None],
                           "strike": [100.0], "opt_kind": ["call"], **cols})
        return df.rename(columns={"volatility": "iv", "day_volume": "volume",
                                  "oi_ch": "oi_change"})


def test_full_set_is_requested_first():
    c = _Client(lambda p: True)
    df = c.chain("SPX")
    assert c.asked == [cx._CHAIN_PARAMS]
    for col in cx._OPTIONAL_OUT_COLS:
        assert col in df.columns


def test_new_flow_params_rejected_falls_back_but_keeps_oi_change():
    """A symbol that doesn't serve the new params must still get oi_change."""
    c = _Client(lambda p: not any(x in p for x in cx._CHAIN_FLOW_PARAMS))
    df = c.chain("XYZ")
    assert len(c.asked) == 2
    assert c.asked[1] == (*cx._CHAIN_CORE_PARAMS, *cx._CHAIN_OI_CH)
    assert df["oi_change"].notna().all()  # the field we had before still works
    for col in cx._CHAIN_FLOW_PARAMS:
        assert col in df.columns and df[col].isna().all()


def test_everything_optional_rejected_falls_to_core():
    c = _Client(lambda p: "oi_ch" not in p and not any(x in p for x in cx._CHAIN_FLOW_PARAMS))
    df = c.chain("XYZ")
    assert len(c.asked) == 3
    assert c.asked[2] == cx._CHAIN_CORE_PARAMS
    for col in cx._OPTIONAL_OUT_COLS:
        assert col in df.columns and df[col].isna().all()
    assert df["delta"].notna().all()  # core survived


def test_core_failure_still_raises():
    c = _Client(lambda p: False)
    with pytest.raises(DataSourceError):
        c.chain("XYZ")
    assert len(c.asked) == 3


def test_every_caller_sees_the_same_columns_whatever_tier_won():
    full = _Client(lambda p: True).chain("SPX")
    degraded = _Client(lambda p: False if any(x in p for x in cx._CHAIN_FLOW_PARAMS) else True).chain("SPX")
    assert set(cx._OPTIONAL_OUT_COLS) <= set(full.columns)
    assert set(cx._OPTIONAL_OUT_COLS) <= set(degraded.columns)


def test_param_tuples_are_disjoint_and_complete():
    assert set(cx._CHAIN_PARAMS) == set(cx._CHAIN_CORE_PARAMS) | set(cx._CHAIN_OI_CH) | set(cx._CHAIN_FLOW_PARAMS)
    assert len(cx._CHAIN_PARAMS) == len(set(cx._CHAIN_PARAMS))
    # The widening must not have dropped anything the old pull had.
    for p in ("delta", "gamma", "volatility", "oi", "oi_ch", "day_volume",
              "gxoi", "dxoi", "vxoi", "multiplier", "vanna", "charm", "theta", "vega"):
        assert p in cx._CHAIN_PARAMS
