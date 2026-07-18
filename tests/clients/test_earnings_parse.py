"""Tests for the defensive earn_cal parser — pure, no HTTP.

Covers the date-encoding coercion (day_id / epoch-seconds / ISO) and the reply
shapes, including the CONFIRMED live ConvexValue ``{"data": [header, rows]}`` shape
where ``rows`` is nested one level.
"""

from __future__ import annotations

from datetime import date

from trading_intel.clients.earnings_parse import parse_earnings_calendar


def test_header_rows_shape_mixed_date_encodings():
    raw = {
        "data": [
            ["symbol", "date", "timing"],
            ["NFLX", 20650, "AMC"],  # day_id int
            ["AAPL", "2026-07-31", "BMO"],  # ISO
            ["", "2026-08-01", "AMC"],  # missing symbol -> skipped
        ]
    }
    out = parse_earnings_calendar(raw)
    assert [e.symbol for e in out] == ["NFLX", "AAPL"]
    aapl = out[1]
    assert aapl.date == date(2026, 7, 31)
    assert aapl.session == "BMO"
    assert isinstance(out[0].date, date)  # day_id decoded
    assert out[0].session == "AMC"


def test_list_of_dicts_shape():
    raw = {"data": [{"symbol": "MSFT", "date": "2026-07-29", "session": "after market"}]}
    (e,) = parse_earnings_calendar(raw)
    assert e.symbol == "MSFT"
    assert e.date == date(2026, 7, 29)
    assert e.session == "AMC"


def test_epoch_seconds_date():
    raw = {"data": [["ticker", "report_date"], ["TSLA", 1_800_000_000]]}
    (e,) = parse_earnings_calendar(raw)
    assert e.symbol == "TSLA"
    assert e.date.year >= 2027  # 1.8e9 epoch-seconds is in 2027


def test_garbage_returns_empty():
    assert parse_earnings_calendar(None) == []
    assert parse_earnings_calendar({"data": []}) == []
    assert parse_earnings_calendar({"nope": 1}) == []


def test_real_earn_cal_schema_confirmed_2026_07_18():
    """Locks the CONFIRMED live earn_cal shape: data = [header, [rows...]]."""
    raw = {
        "data": [
            [
                "date", "symbol", "eps", "eps_estimated", "time", "revenue",
                "revenue_estimated", "fiscal_date_ending", "updated_from_date",
            ],
            [
                ["2026-07-20", "NNOX", None, -0.14667, "bmo", None, 5926000.0, "2026-03-31", "2026-04-21"],
                ["2026-07-20", "SFBS", None, 1.59, "amc", None, 167613700.0, "2026-06-30", "2026-04-21"],
                ["2026-07-20", "WASH", None, 0.85, None, None, 59910500.0, "2026-06-30", "2026-04-21"],
            ],
        ]
    }
    out = parse_earnings_calendar(raw)
    assert [e.symbol for e in out] == ["NNOX", "SFBS", "WASH"]
    assert all(str(e.date) == "2026-07-20" for e in out)
    assert [e.session for e in out] == ["BMO", "AMC", None]
