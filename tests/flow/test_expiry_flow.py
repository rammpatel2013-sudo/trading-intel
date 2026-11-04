"""Phase 5 E1 — expiry-flow buckets (pure)."""

from datetime import date, timedelta

from trading_intel.flow import expiry_flow as ef

D = date(2026, 11, 4)


def test_premium_signs_and_buckets():
    rows = [
        (D, D + timedelta(days=3), "C", 100.0, 20.0),
        (D, D + timedelta(days=45), "P", 50.0, 10.0),
    ]
    out = ef.premium_by_bucket(rows)
    assert out["0–7d"]["bull"] == 100.0 and out["0–7d"]["bear"] == 20.0
    assert out["31–60d"]["bear"] == 50.0 and out["31–60d"]["bull"] == 10.0
    assert out["0–7d"]["net"] == 80.0


def test_demand_write_quadrants():
    rows = [
        (5, "C", 100, 0.30, 0.28),
        (5, "C", 50, 0.27, 0.28),
        (5, "P", -40, 0.25, 0.26),
        (5, "P", -10, 0.27, 0.26),
        (5, "C", 7, 0.28, 0.28),
    ]
    o = ef.demand_write(rows)["0–7d"]
    assert o["call_bought_open"] == 100 and o["call_written_open"] == 50
    assert o["put_long_close"] == 40 and o["put_short_cover"] == 10 and o["no_read"] == 7
    assert abs(o["call_demand_share"] - 100 / 150) < 1e-3
    assert ef.plain_read(ef.premium_by_bucket([]), {"0–7d": o})


def test_oi_bucket_and_bucket_edges():
    assert ef.bucket_of(0) == "0–7d" and ef.bucket_of(8) == "8–30d" and ef.bucket_of(61) == "60d+"
    o = ef.oi_by_bucket([(10, "C", 100), (10, "P", 150)])["8–30d"]
    assert o["pc_ratio"] == 1.5
