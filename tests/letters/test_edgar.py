"""Tests for the 13F parser + QoQ diff (pure, no I/O)."""

from __future__ import annotations

from trading_intel.letters.edgar import (
    Holding,
    diff_holdings,
    holdings_from_fmp,
    parse_infotable,
)

_INFO = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable>
  <nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>150000</value>
  <shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
</infoTable>
<infoTable>
  <nameOfIssuer>MICROSOFT CORP</nameOfIssuer><cusip>594918104</cusip><value>90000</value>
  <shrsOrPrnAmt><sshPrnamt>500</sshPrnamt></shrsOrPrnAmt>
</infoTable>
<infoTable>
  <nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>50000</value>
  <shrsOrPrnAmt><sshPrnamt>300</sshPrnamt></shrsOrPrnAmt>
</infoTable>
</informationTable>"""


def test_parse_infotable_aggregates_and_sorts():
    holdings = parse_infotable(_INFO)
    assert [h.cusip for h in holdings] == ["037833100", "594918104"]  # AAPL first by value
    aapl = holdings[0]
    assert aapl.issuer == "APPLE INC"
    assert aapl.value_usd == 200000.0  # 150000 + 50000 aggregated
    assert aapl.shares == 1300.0
    assert holdings[1].value_usd == 90000.0


def test_diff_holdings_classifies_changes():
    prev = [
        Holding("APPLE INC", "037833100", 100000, 1000),
        Holding("TESLA INC", "88160R101", 40000, 200),
    ]
    cur = [
        Holding("APPLE INC", "037833100", 200000, 1300),  # added (shares up)
        Holding("MICROSOFT CORP", "594918104", 90000, 500),  # new
    ]
    by = {c.cusip: c for c in diff_holdings(prev, cur)}
    assert by["037833100"].kind == "added"
    assert by["594918104"].kind == "new"
    assert by["88160R101"].kind == "exited"


def test_holdings_from_fmp_parses_tickers_and_aggregates():
    rows = [
        {"symbol": "AAPL", "securityName": "APPLE INC", "cusip": "037833100", "shares": 1000, "marketValue": 150000},
        {"symbol": "MSFT", "nameOfIssuer": "MICROSOFT CORP", "cusip": "594918104", "shares": 500, "value": 90000},
        {"ticker": "AAPL", "name": "APPLE INC", "cusip": "037833100", "sharesNumber": 300, "marketValueUsd": 50000},
    ]
    holdings = holdings_from_fmp(rows)
    assert [h.ticker for h in holdings] == ["AAPL", "MSFT"]  # AAPL first by value
    assert holdings[0].value_usd == 200000.0
    assert holdings[0].shares == 1300.0
    assert holdings[0].issuer == "APPLE INC"


def test_diff_keys_on_ticker_when_present():
    prev = [Holding("APPLE INC", "037833100", 100000, 1000, "AAPL")]
    cur = [
        Holding("APPLE INC", "037833100", 200000, 1300, "AAPL"),
        Holding("NVIDIA CORP", "67066G104", 80000, 400, "NVDA"),
    ]
    by = {c.ticker: c for c in diff_holdings(prev, cur)}
    assert by["AAPL"].kind == "added"
    assert by["NVDA"].kind == "new"
