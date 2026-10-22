from datetime import date

from trading_intel.expiry_clock import monthly_opex, next_events, phase, third_friday, vix_expiry_for


def test_third_friday_and_opex():
    assert third_friday(2026, 10) == date(2026, 10, 16)
    assert monthly_opex(2026, 12) == date(2026, 12, 18)


def test_vix_expiry_is_30_days_before_next_third_friday():
    # Nov 20 2026 third Friday → Oct 21 2026 (Alma's "VIX expiry" post was 2026-10-21)
    assert vix_expiry_for(2026, 10) == date(2026, 10, 21)
    assert vix_expiry_for(2026, 11) == date(2026, 11, 18)
    assert vix_expiry_for(2026, 12) == date(2026, 12, 16)


def test_next_events_order_and_kinds():
    ev = next_events(date(2026, 10, 22))
    kinds = [(e["kind"], e["date"]) for e in ev[:5]]
    assert kinds[0] == ("month_end", "2026-10-30")
    assert ("vixpiration", "2026-11-18") in kinds
    assert ("opex", "2026-11-20") in kinds
    assert ("quarterly_opex", "2026-12-18") in kinds


def test_phase_reads():
    assert phase(date(2026, 10, 16))["reads"][0].startswith("OpEx day")
    assert phase(date(2026, 10, 21))["reads"][0].startswith("VIXpiration")
    p = phase(date(2026, 10, 22))
    assert p["since_opex_days"] == 6 and p["last_opex"] == "2026-10-16"
