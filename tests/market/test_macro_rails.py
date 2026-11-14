"""tests for trading_intel/market/macro_rails.py (#60 group, 2026-11-13 overnight)."""

from datetime import date, timedelta

from trading_intel.market import macro_rails as mr


def ser(vals, start=date(2026, 9, 1)):
    return [(start + timedelta(days=i), float(v)) for i, v in enumerate(vals)]


def test_credit_spark_and_flags():
    hy = ser([3.00] * 25 + [3.14])  # +14 bp today
    up = ser(list(range(100, 126)))  # at a 20-day high
    flat = ser([1.0] * 26)
    c = mr.credit_spark(hy, up, up, ser([1.0] * 25 + [0.98]), ser([90] * 25 + [95]))
    assert c["found"] and c["spark"] and c["hy_chg_bp"] == 14.0 and c["state"] == "red"
    assert c["non_confirm"] is True and c["credit_veto"] is True and c["move_chg1"] == 5.0
    c2 = mr.credit_spark(ser([3.0] * 26), flat, flat, flat, ser([90] * 26))
    assert c2["state"] == "green" and not c2["flags"] and c2["hy_at_20d_low"]


def test_credit_short_history():
    assert mr.credit_spark(ser([3.0] * 5), [], [], [], [])["found"] is False


def test_vix_monthly_dates():
    assert mr.is_vix_monthly(date(2026, 11, 18))
    assert mr.is_vix_monthly(date(2026, 12, 16))
    assert mr.is_vix_monthly(date(2027, 1, 20))
    assert not mr.is_vix_monthly(date(2026, 11, 25))  # a weekly


def test_vx_curve_states():
    ex = [
        {"e": "2026-11-18", "F": 18.0},
        {"e": "2026-11-25", "F": 18.4},
        {"e": "2026-12-16", "F": 19.1},
        {"e": "2027-01-20", "F": 19.8},
    ]
    v = mr.vx_curve(ex, date(2026, 11, 13))
    assert v["found"] and [x["e"] for x in v["vx"]] == ["2026-11-18", "2026-12-16", "2027-01-20"]
    assert v["vx2_vx1"] == 1.1 and v["state"] == "contango" and v["color"] == "green"
    inv = mr.vx_curve(
        [
            {"e": "2026-11-18", "F": 25.0},
            {"e": "2026-12-16", "F": 23.0},
            {"e": "2027-01-20", "F": 22.0},
        ],
        date(2026, 11, 13),
    )
    assert inv["state"] == "backwardation" and inv["front_inverted"] and inv["color"] == "red"
    assert mr.vx_curve(ex[:1], date(2026, 11, 13))["found"] is False


def test_event_gate():
    closes = ser([100 + (3 if i % 10 == 0 else 0.3) * (1 if i % 2 else -1) for i in range(60)])
    ev_days = [d for i, (d, _) in enumerate(closes) if i % 10 == 0 and i]
    h = mr.move_ratio(closes, ev_days)
    assert h["n"] == len(ev_days) and h["ratio"] > 1.5
    g = mr.event_gate(
        [
            {"d": "2026-11-19", "t": "08:30", "kind": "Data", "label": "CPI (Oct)"},
            {"d": "2026-11-20", "t": "", "kind": "Expiry", "label": "Monthly opex"},
            {"d": "2026-12-09", "t": "14:00", "kind": "Fed", "label": "FOMC decision"},
        ],
        {"CPI": h},
        90.0,
    )
    assert [x["kind"] for x in g] == ["CPI", "FOMC"]
    assert g[0]["color"] == "red" and g[0]["size"] == 0.5 and g[1]["color"] == "green"
    assert mr.gate_color(1.6, 10.0) == ("yellow", 0.75) and mr.gate_color(None, None) == (
        "green",
        1.0,
    )
