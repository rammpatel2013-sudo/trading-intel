"""tests for trading_intel/market/vomma_zomma.py (#60 group, 2026-11-13 overnight)."""

import math
from datetime import date, datetime

from trading_intel.market import vomma_zomma as vz


def test_greeks_match_finite_differences():
    s, k, sig, t = 6000.0, 6050.0, 0.18, 20 / 365
    g, v, vo, zo = vz._greeks(s, k, sig, t)
    h = 1e-4
    _, v_up, _, _ = vz._greeks(s, k, sig + h, t)
    _, v_dn, _, _ = vz._greeks(s, k, sig - h, t)
    g_up = vz._greeks(s, k, sig + h, t)[0]
    g_dn = vz._greeks(s, k, sig - h, t)[0]
    assert math.isclose(vo, (v_up - v_dn) / (2 * h), rel_tol=1e-4)
    assert math.isclose(zo, (g_up - g_dn) / (2 * h), rel_tol=1e-4)


def test_exposures_signs_and_buckets():
    asof = datetime(2026, 11, 13, 16, 15)
    rows = [
        (date(2026, 11, 20), 6000.0, "C", 1000, 0.15),
        (date(2026, 11, 20), 6000.0, "P", 1000, 0.15),
        (date(2026, 12, 31), 6000.0, "C", 500, 0.17),
        (date(2026, 11, 13), 6000.0, "C", 9999, 0.2),
    ]
    e = vz.exposures(rows, 6000.0, asof)
    b = e["buckets"]
    assert (
        abs(b["0-7"]["gex_bn"]) < 1e-9 and b["0-7"]["oi"] == 2000
    )  # call long + put short cancel; expired dropped
    assert b["31-90"]["gex_bn"] > 0 and b["31-90"]["oi"] == 500
    assert "2026-11-13" not in e["expiries"]


def test_flags():
    def h(d, vom, gex, mz=None):
        x = {
            "date": d,
            "buckets": {
                n: {"gex_bn": 0.0, "vomma_mn": 0.0, "zomma_bn": 0.0, "oi": 0}
                for n, _, _ in vz.BUCKETS
            },
            "expiries": {},
        }
        x["buckets"]["31-90"]["vomma_mn"] = vom
        x["buckets"]["8-30"]["gex_bn"] = gex
        if mz is not None:
            x["expiries"]["2026-11-20"] = {"zomma_bn": mz}
        return x

    hist = [h(f"2026-10-{i:02d}", 1.0, 5.0, mz=1.0) for i in range(1, 21)] + [
        h("2026-11-13", 2.0, 3.0, mz=0.4)
    ]
    f = vz.flags(hist)
    assert f["vomma_vs_base"] == 2.0 and f["gex_vs_base"] == 0.6 and f["monthly"] == "2026-11-20"
    assert f["monthly_zomma_ratio_5"] == 0.4 and len(f["flags"]) == 2
    assert vz.flags([])["found"] is False
    assert (
        vz.nearest_monthly(["2026-11-16", "2026-11-20", "2026-12-18"], date(2026, 11, 14))
        == "2026-11-20"
    )
