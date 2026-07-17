"""Tests for the flow-intelligence read-side aggregations — pure."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from trading_intel.flow.intelligence import (
    build_flow_payload,
    net_premium_4way,
    premium_by_dte,
    premium_by_size,
)

_REF = date(2026, 7, 16)


def _prints(rows: list[dict]) -> pd.DataFrame:
    base = {"cp": "C", "side": "buy", "notional": 0.0, "size": 1, "expiry": _REF}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_net_premium_4way_matches_board_shape():
    df = _prints(
        [
            {"cp": "C", "side": "buy", "notional": 6_000_000},
            {"cp": "P", "side": "sell", "notional": 59_000_000},
            {"cp": "P", "side": "buy", "notional": 19_000_000},
            {"cp": "C", "side": "sell", "notional": 16_000_000},
        ]
    )
    fw = net_premium_4way(df)
    assert fw.call_buy == pytest.approx(6_000_000)
    assert fw.put_sell == pytest.approx(59_000_000)
    assert fw.put_buy == pytest.approx(19_000_000)
    assert fw.call_sell == pytest.approx(16_000_000)
    assert fw.bullish_premium == pytest.approx(65_000_000)  # call-buy + put-sell
    assert fw.bearish_premium == pytest.approx(35_000_000)  # put-buy + call-sell
    assert fw.net_premium == pytest.approx(30_000_000)


def test_4way_excludes_unclassified_prints():
    df = _prints(
        [
            {"cp": "C", "side": "buy", "notional": 6_000_000},
            {"cp": "C", "side": "mid", "notional": 100_000_000},  # can't classify -> excluded
            {"cp": "P", "side": "unknown", "notional": 100_000_000},
        ]
    )
    fw = net_premium_4way(df)
    assert fw.call_buy == pytest.approx(6_000_000)
    assert fw.put_sell == 0.0 and fw.put_buy == 0.0 and fw.call_sell == 0.0


def test_premium_by_size_buckets():
    df = _prints(
        [
            {"notional": 10_000},  # retail (< 25k)
            {"notional": 100_000},  # medium
            {"notional": 500_000},  # large (>= 250k)
        ]
    )
    sizes = premium_by_size(df)
    assert sizes["retail"] == pytest.approx(10_000)
    assert sizes["medium"] == pytest.approx(100_000)
    assert sizes["large"] == pytest.approx(500_000)


def test_premium_by_dte_buckets():
    df = _prints(
        [
            {"notional": 1, "expiry": _REF + pd.Timedelta(days=3)},  # <7
            {"notional": 1, "expiry": _REF + pd.Timedelta(days=20)},  # 7-31
            {"notional": 1, "expiry": _REF + pd.Timedelta(days=60)},  # 31-93
            {"notional": 1, "expiry": _REF + pd.Timedelta(days=200)},  # >93
        ]
    )
    dte = premium_by_dte(df, ref=_REF)
    assert dte == {"<7": 1.0, "7-31": 1.0, "31-93": 1.0, ">93": 1.0}


def test_empty_frame_is_safe():
    empty = pd.DataFrame()
    assert net_premium_4way(empty).net_premium == 0.0
    assert premium_by_size(empty) == {"retail": 0.0, "medium": 0.0, "large": 0.0}
    assert all(v == 0.0 for v in premium_by_dte(empty, ref=_REF).values())


def test_build_flow_payload_shape():
    df = _prints(
        [
            {"cp": "C", "side": "buy", "notional": 6_000_000},
            {"cp": "P", "side": "sell", "notional": 59_000_000},
            {"cp": "P", "side": "buy", "notional": 19_000_000},
            {"cp": "C", "side": "sell", "notional": 16_000_000},
        ]
    )
    daily = {"dominant_side": "sell", "pct_buy": 0.30, "net_dollar_delta": -30_000_000}
    contracts = [{"expiry": "2026-07-17", "strike": 190.0, "cp": "P", "dominant_side": "sell"}]
    p = build_flow_payload("orcl", _REF, df, daily=daily, contracts=contracts)
    assert p["symbol"] == "ORCL"
    assert p["trade_date"] == _REF.isoformat()
    assert p["n_prints"] == 4
    assert p["net_premium_4way"]["net_premium"] == pytest.approx(30_000_000)
    assert p["accumulation"]["dominant_side"] == "sell"
    assert p["top_contracts"][0]["strike"] == 190.0
