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
    assert vix_options.VIX_EXPS == tuple(range(1, 11))
    # 2026-11-14: must reach the listed ~100 calls at VIX ~19 (was 1.5 -> stopped at 47.5)
    assert vix_options.VIX_STRIKE_RANGE >= 4.0


def test_vix_options_drops_phantom_expiry() -> None:
    """2026-11-25 came back with iv 0 / oi 0 / volume 0 on every row (holiday-shifted weekly)."""
    import pandas as pd

    rows = []
    for exp, iv, oi in (("2026-11-24", 0.9, 120.0), ("2026-11-25", 0.0, 0.0)):
        for k in (18.0, 20.0, 22.0):
            for kind in ("call", "put"):
                rows.append({"expiration": exp, "strike": k, "opt_kind": kind,
                             "delta": 0.0 if iv == 0 else 0.5, "iv": iv, "oi": oi, "volume": 0.0})
    # one live row with only volume keeps its expiry
    rows.append({"expiration": "2026-12-01", "strike": 20.0, "opt_kind": "call",
                 "delta": None, "iv": None, "oi": None, "volume": 5.0})
    recs = vix_options._records_from_chain(pd.DataFrame(rows), as_of=date(2026, 11, 13))
    exps = {r["expiration"].isoformat() for r in recs}
    assert exps == {"2026-11-24", "2026-12-01"}
    assert len(recs) == 7


def test_iv_tenor_and_index_skew_skip_weekends(monkeypatch) -> None:
    """2026-10-29: weekend runs had banked Friday's values as Sat/Sun rows."""
    from datetime import datetime

    from trading_intel.scheduler.jobs import index_skew, iv_tenor_snapshots

    iv_tenor_snapshots.run(_Boom(), _Boom(), settings=object(), as_of=date(2026, 10, 25))  # Sunday
    monkeypatch.setattr(index_skew, "eastern_now", lambda: datetime(2026, 10, 24, 17, 0))  # Saturday
    index_skew.run(_Boom(), _Boom(), settings=object())


def test_index_skew_oi_share_keeps_legacy_window() -> None:
    """2026-11-14: the widened VIX pull must not shift vix_call_oi_share's history."""
    import pandas as pd

    from trading_intel.scheduler.jobs.index_skew import _legacy_oi_window

    exps = pd.date_range("2026-11-18", periods=10, freq="7D")
    rows = [{"expiration": e, "strike": k, "opt_kind": "call", "delta": 0.1, "oi": 1.0}
            for e in exps for k in (20.0, 45.0, 60.0, 90.0)]
    out = _legacy_oi_window(pd.DataFrame(rows), 19.3)
    assert out["expiration"].nunique() == 8
    assert out["strike"].max() == 45.0  # 19.3 x 2.5 = 48.25
    assert len(_legacy_oi_window(pd.DataFrame(rows), None)) == 32
