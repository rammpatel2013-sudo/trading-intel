"""Tests for the FMP -> SentimentInputs mapping — pure, tolerant, no vendor."""

from __future__ import annotations

from trading_intel.sentiment.fmp_map import extract_inputs


def test_extract_full_payloads():
    inst = [
        {
            "ownershipPercent": 45.1,
            "investorsHolding": 3723,
            "numberOf13Fshares": 1.26e9,
            "numberOf13FsharesChange": 29.3e6,
            "newPositions": 210,
            "closedPositions": 180,
            "putCallRatio": 0.8,
        }
    ]
    targets = {"targetConsensus": 252.0, "targetHigh": 400.0, "targetLow": 164.0}
    grades = {"strongBuy": 20, "buy": 17, "hold": 5, "sell": 1, "strongSell": 0, "consensus": "Buy"}
    quote = [{"symbol": "ORCL", "price": 133.6}]

    inp = extract_inputs("ORCL", inst=inst, targets=targets, grades=grades, quote=quote)

    assert inp.inst_pct == 45.1
    assert inp.inst_holders == 3723
    assert inp.pt_avg == 252.0 and inp.pt_high == 400.0 and inp.pt_low == 164.0
    assert inp.rating_buy == 37 and inp.rating_hold == 5 and inp.rating_sell == 1
    assert inp.num_analysts == 43
    assert inp.rating_consensus == "Buy"
    assert inp.price == 133.6


def test_missing_endpoints_degrade_to_none():
    inp = extract_inputs("X", inst=None, targets=None, grades=None, quote=None)
    assert inp.symbol == "X"
    assert inp.pt_avg is None
    assert inp.rating_buy is None
    assert inp.num_analysts is None
    assert inp.rating_consensus is None


def test_string_numbers_are_parsed():
    inp = extract_inputs("X", targets={"targetConsensus": "150.5"}, quote={"price": "100"})
    assert inp.pt_avg == 150.5
    assert inp.price == 100.0
