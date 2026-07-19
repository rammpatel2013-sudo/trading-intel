"""Tests for the deterministic VIX expiration calendar (``vol.vix_calendar``)."""

from __future__ import annotations

from datetime import date

import pytest

from trading_intel.vol.vix_calendar import (
    is_market_holiday,
    next_vix_expirations,
    third_friday,
    us_market_holidays,
    vix_expiration_for_settlement_month,
)


def test_third_friday_known_dates() -> None:
    assert third_friday(2025, 7) == date(2025, 7, 18)
    assert third_friday(2026, 6) == date(2026, 6, 19)
    assert third_friday(2026, 1) == date(2026, 1, 16)


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        # June 2025 contract → 30d before July's 3rd Friday (Jul 18) = Wed Jun 18.
        (2025, 6, date(2025, 6, 18)),
        # July 2025 → 30d before Aug 15 = Wed Jul 16.
        (2025, 7, date(2025, 7, 16)),
        # June 2026 → 30d before Jul 17 = Wed Jun 17.
        (2026, 6, date(2026, 6, 17)),
        # December rollover → uses next January's 3rd Friday.
        (2025, 12, date(2025, 12, 17)),
    ],
)
def test_vix_expiration_known_months(year: int, month: int, expected: date) -> None:
    assert vix_expiration_for_settlement_month(year, month) == expected


def test_vix_expiration_always_midweek_or_rolled() -> None:
    """Every computed expiry is a business day (Wed normally, Tue if rolled)."""
    for month in range(1, 13):
        exp = vix_expiration_for_settlement_month(2026, month)
        assert not is_market_holiday(exp)
        assert exp.weekday() in (1, 2)  # Tuesday (rolled) or Wednesday (normal)


def test_market_holidays_include_good_friday_and_juneteenth() -> None:
    hols = us_market_holidays(2026)
    assert date(2026, 4, 3) in hols  # Good Friday 2026
    assert date(2026, 6, 19) in hols  # Juneteenth
    assert date(2026, 1, 1) in hols  # New Year's
    # Columbus Day is a federal holiday but the equity market is open.
    assert date(2026, 10, 12) not in hols


def test_next_expirations_chronological_and_future() -> None:
    exps = next_vix_expirations(date(2026, 6, 14), n=4)
    assert exps == sorted(exps)
    assert len(exps) == len(set(exps))
    assert all(e >= date(2026, 6, 14) for e in exps)
    # Next expiry after Jun 14, 2026 is the Jun 17 settlement.
    assert exps[0] == date(2026, 6, 17)


def test_next_expirations_includes_current_month_if_future() -> None:
    # On Jun 1 the Jun 17 expiry is still ahead and must be the first returned.
    exps = next_vix_expirations(date(2026, 6, 1), n=1)
    assert exps[0] == date(2026, 6, 17)
