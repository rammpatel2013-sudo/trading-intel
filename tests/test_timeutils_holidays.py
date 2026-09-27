"""NYSE session calendar covers 2025–2027."""

from datetime import date

from trading_intel.timeutils import is_trading_session


def test_holidays_across_years() -> None:
    for d in (date(2025, 4, 18), date(2026, 4, 3), date(2027, 3, 26), date(2027, 12, 24)):
        assert not is_trading_session(d)
    assert is_trading_session(date(2026, 10, 1))
    assert not is_trading_session(date(2026, 9, 27))  # Sunday
