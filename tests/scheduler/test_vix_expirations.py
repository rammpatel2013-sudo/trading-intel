"""Tests for the VIX-expirations job row builder (pure, no DB)."""

from __future__ import annotations

from datetime import date, timedelta

from trading_intel.scheduler.jobs.vix_expirations import (
    HORIZON_MONTHS,
    LOOKBACK_MONTHS,
    build_rows,
)


def test_build_rows_window_size_and_shape() -> None:
    rows = build_rows(date(2026, 6, 14))
    assert len(rows) == LOOKBACK_MONTHS + HORIZON_MONTHS + 1
    for r in rows:
        assert set(r) == {"expiration", "spx_ref_expiry", "holiday_adjusted", "updated_at"}
        # The VIX expiry is exactly 30 days before its paired SPX third Friday,
        # unless it was holiday-rolled (then strictly earlier).
        gap = (r["spx_ref_expiry"] - r["expiration"]).days
        assert gap >= 30
        if not r["holiday_adjusted"]:
            assert gap == 30
        assert r["updated_at"] == date(2026, 6, 14)


def test_build_rows_chronological_and_unique() -> None:
    rows = build_rows(date(2026, 6, 14))
    exps = [r["expiration"] for r in rows]
    assert exps == sorted(exps)
    assert len(exps) == len(set(exps))


def test_build_rows_flags_juneteenth_roll() -> None:
    # The May 2026 contract pairs with the June 19, 2026 SPX expiry (Juneteenth),
    # so it must be holiday-adjusted off the normal Wednesday.
    rows = build_rows(date(2026, 5, 1))
    may = [r for r in rows if r["expiration"].year == 2026 and r["expiration"].month == 5]
    assert may and may[0]["holiday_adjusted"] is True
    assert may[0]["expiration"] == date(2026, 5, 19)


def test_build_rows_includes_recent_past_for_joins() -> None:
    as_of = date(2026, 6, 14)
    rows = build_rows(as_of)
    assert any(r["expiration"] < as_of for r in rows)  # lookback window present
    assert any(r["expiration"] >= as_of for r in rows)  # upcoming present
    earliest = min(r["expiration"] for r in rows)
    assert earliest >= as_of - timedelta(days=31 * (LOOKBACK_MONTHS + 1))
