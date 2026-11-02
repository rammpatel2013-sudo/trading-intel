"""Breadth rebuild (2026-10-29): rebuilt rows equal a correctly timed live run."""

from __future__ import annotations

import random
from datetime import date, timedelta

from trading_intel.market import breadth as bm
from trading_intel.market import breadth_rebuild as br


def _sessions(n: int, start: date = date(2025, 1, 2)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _dated(n_sym: int = 40, n_days: int = 400, seed: int = 7):
    rnd = random.Random(seed)
    days = _sessions(n_days)
    dated = {}
    for k in range(n_sym):
        px, series = 100.0, {}
        for d in days:
            px *= 1 + rnd.gauss(0.0003, 0.015)
            series[d] = round(px, 4)
        dated[f"S{k}"] = series
    dated["S0"].pop(days[-3])  # a missing bar
    return dated, days


def test_per_day_counts_match_compute_breadth_on_the_live_window():
    dated, days = _dated()
    d = days[-1]
    counts = br.per_day_counts(dated, [d])[d]
    window = {
        s: [v for k, v in sorted(by.items()) if k <= d][-br.WINDOW:] for s, by in dated.items()
    }
    live = bm.compute_breadth(window)
    assert counts["advancers"] == live.advancers and counts["decliners"] == live.decliners
    assert counts["new_highs"] == live.new_highs and counts["new_lows"] == live.new_lows
    assert counts["pct_above_50"] == live.pct_above_50
    assert counts["pct_above_200"] == live.pct_above_200


def test_symbol_without_a_bar_that_day_is_left_out():
    dated, days = _dated()
    d = days[-3]
    assert br.per_day_counts(dated, [d])[d]["n_constituents"] == len(dated) - 1


def test_assemble_chains_ad_line_mcclellan_and_bull_bear_line():
    dated, days = _dated()
    span = days[-120:]
    counts = br.per_day_counts(dated, span)
    bars = [(d, 5000.0 + i, 4990.0 + i) for i, d in enumerate(days)]
    rows = br.assemble(counts, bars)
    nets = [counts[d]["advancers"] - counts[d]["decliners"] for d in span]
    assert [r["ad_line"] for r in rows][-1] == sum(nets)
    # McClellan of the last row = live semantics: newest 80 prior nets + today
    osc, _ = bm.mcclellan(nets[-81:-1] + [nets[-1]], None)
    assert abs(rows[-1]["mcclellan_osc"] - osc) < 1e-9
    # Bull/Bear Line ratchets on the all-time high, history before the span included
    assert abs(rows[-1]["bull_bear_line"] - 0.9 * (5000.0 + len(days) - 1)) < 1e-9
    assert rows[-1]["spx_close"] == 4990.0 + len(days) - 1


def test_sp600_mode_is_ad_only():
    dated, days = _dated()
    counts = br.per_day_counts(dated, days[-5:], full=False)
    rows = br.assemble(counts, full=False)
    assert set(rows[-1]) == {"ts", "advancers", "decliners", "n_constituents", "net_adv", "ad_line"}


def test_data_session_picks_the_majority_day():
    assert bm.data_session({"A": "2026-10-23", "B": "2026-10-23", "C": "2026-10-22"}) == "2026-10-23"
    assert bm.data_session({}) is None
