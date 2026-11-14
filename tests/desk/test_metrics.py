"""Desk metrics vs TailThatWagsDog's 11/13 brief (2026-11-14)."""

from trading_intel.desk import metrics as m


def test_dbr_windows_match_his_awk():
    # AWK FINRA short / total share 11/02-11/13 reproduce 3.15 / 2.65 / 2.35 when the ratios are right
    one = m.dbr([(759, 1000)])
    assert round(one, 2) == 3.15
    w = m.dark_windows([(726, 1000)] * 5 + [(678, 1000)] * 5)
    assert round(w["w1"], 2) == 2.65 and w["pass"] and w["trend"] == "accumulating"
    assert not m.dark_windows([(436, 1000)] * 10)["pass"]  # KDP-like (0.77)
    assert m.dark_windows([(700, 1000)] * 3)["w1"] is None  # needs 5 sessions


def test_bsr_and_changes_match_his_numbers():
    assert m.bsr(150.28, 147.90, 149.60) == 2.5  # AWK
    assert abs(m.bsr(39.61, 37.50, 39.42) - 10.1) < 0.02  # YETI 10.1
    assert m.bsr(10, 9, 10) == 99.9 and m.bsr(10, 10, 10) is None
    awk = [149.22, 150.36, 149.66, 147.58, 149.6]
    d1, d5 = m.changes([150.29, *awk])
    assert round(d1, 2) == 1.37 and round(d5, 2) == -0.46


def test_bullish_and_leverage_and_groups():
    assert m.bullish_pct(60, 20, 10, 10) == 70.0
    assert m.bullish_pct(0, 0, 0, 0) is None and m.bullish_pct(None, 1, 1, 1) is None
    assert m.leverage("SOXS") == -3.0 and m.leverage("PSQ") == -1.0
    assert m.leverage("ZZZQ", "Tradr 2X Short ZZZ Daily ETF") == -2.0
    assert m.group_for("Consumer Defensive", "Packaged Foods") == "Consumer Staples"
    assert m.group_for("Consumer Cyclical", "Restaurants") == "Travel, Leisure & Restaurants"
    assert m.group_for("Industrials", "Residential Construction") == "Housing, Building & REITs"
    assert m.group_for("Technology", "Software - Infrastructure") == "Tech & Communications"
