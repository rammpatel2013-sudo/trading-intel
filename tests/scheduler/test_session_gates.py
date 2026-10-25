"""vol_surface_cm + vix_options skip non-sessions (the Sat 2026-10-24 ghost row)."""

from __future__ import annotations

from datetime import date

from trading_intel.scheduler.jobs import vix_options, vol_surface_cm


class _Boom:
    def __getattr__(self, name):  # any source/session use = failure
        raise AssertionError(f"{name} must not be called on a non-session")


def test_vol_surface_cm_skips_saturday() -> None:
    vol_surface_cm.run(_Boom(), _Boom(), as_of=date(2026, 10, 24))  # Saturday


def test_vix_options_widened_request() -> None:
    assert vix_options.VIX_EXPS == tuple(range(1, 9))
    assert vix_options.VIX_STRIKE_RANGE >= 1.2  # must reach strike 40 at VIX ~19
