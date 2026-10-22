"""Skew & vanna pure math."""

from __future__ import annotations

from datetime import date, timedelta

from trading_intel.market import skew_vanna as sv


def _rows(d: date, spot: float, p25: float, c25: float, atm: float) -> list[dict]:
    return [
        dict(ts=d, dte=30, delta=25.0, side="put", iv=p25, spot=spot),
        dict(ts=d, dte=30, delta=25.0, side="call", iv=c25, spot=spot),
        dict(ts=d, dte=30, delta=50.0, side="put", iv=atm, spot=spot),
        dict(ts=d, dte=30, delta=50.0, side="call", iv=atm, spot=spot),
        dict(ts=d, dte=60, delta=25.0, side="put", iv=0.9, spot=spot),  # other rung ignored
    ]


def test_normalized_and_wing_skew_issue_example() -> None:
    s = sv.skew_series(_rows(date(2026, 9, 25), 7742, 0.1423, 0.1097, 0.1196))
    assert len(s) == 1
    assert round(s[0].norm_skew, 3) == 0.273
    assert round(s[0].put_skew, 2) == 2.27 and round(s[0].call_skew, 2) == -0.99


def test_skew_crushed_on_rally_label() -> None:
    rows = []
    for i in range(7):
        d = date(2026, 9, 14) + timedelta(days=i)
        p25 = 0.160 - 0.004 * i  # skew falling
        rows += _rows(d, 7600 + 20 * i, p25, 0.110, 0.120)
    rd = sv.skew_read(sv.skew_series(rows), window=5)
    assert rd["label"] == "skew-crushed-on-rally" and rd["d_skew"] < 0


def test_sessions_filter_drops_weekend_rows() -> None:
    rows = _rows(date(2026, 9, 25), 1, 0.2, 0.1, 0.15) + _rows(date(2026, 9, 27), 1, 0.3, 0.1, 0.15)
    s = sv.skew_series(rows, sessions={date(2026, 9, 25)})
    assert [p.d for p in s] == [date(2026, 9, 25)]


def _prof(vals: list[float], asof: str) -> dict:
    return {"found": True, "as_of": asof, "spot": 100.0,
            "profiles": {"spot_ref": [90.0, 95.0, 100.0, 105.0, 110.0],
                         "vanna": {"all": vals}}}


def test_vanna_inversion_detected() -> None:
    vr = sv.vanna_read(_prof([-3, -1, -0.5, 1, 2], "now"), _prof([1, 2, 3, 2, 1], "then"))
    assert vr.inverted and vr.at_spot_now == -0.5 and vr.at_spot_before == 3
    assert 100 < vr.zero_now < 105
    assert sv.vanna_read(_prof([1, 2, 3, 2, 1], "now"), None).inverted is False


def test_es_per_vol_point_and_vega_fields():
    from trading_intel.market.skew_vanna import es_per_vol_point, vanna_read

    assert es_per_vol_point(1.0, 5000.0) == 1e9 / (50 * 5000)
    assert es_per_vol_point(None, 5000.0) is None
    prof = {"found": True, "spot": 100.0, "as_of": "2026-10-01",
            "profiles": {"spot_ref": [99.0, 100.0, 101.0],
                         "vanna": {"all": [1.0, 2.0, 3.0]},
                         "vega": {"all": [0.1, 0.2, 0.3]}}}
    vr = vanna_read(prof, prof)
    assert vr.es_per_volpt_now == es_per_vol_point(2.0, 100.0)
    assert vr.vega_at_spot_now == 0.2 and vr.vega_at_spot_before == 0.2


def test_bs_vega_matches_finite_difference():
    import numpy as np

    from trading_intel.greeks.black_scholes import bs_call_price, bs_vega

    S, K, s, t = 100.0, np.array([105.0]), np.array([0.3]), np.array([0.25])
    fd = (bs_call_price(S, K, s + 1e-4, t) - bs_call_price(S, K, s - 1e-4, t)) / 2e-4
    assert abs(float(bs_vega(S, K, s, t)[0]) - float(fd[0])) < 1e-3
