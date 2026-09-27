"""Norseman regime math — pinned to the author's published numbers."""

from __future__ import annotations

from datetime import date, timedelta

from trading_intel.market import norseman as nm


def _bars(closes: list[float], *, start: date = date(2026, 1, 5), hi: float = 0.0,
          lo: float = 0.0) -> list[nm.Bar]:
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(nm.Bar(d, c + hi, c - lo, c))
        d += timedelta(days=1)
    return out


def test_line_is_ninety_pct_of_intraday_ath_issue_70() -> None:
    bars = [nm.Bar(date(2026, 8, 13), 7816.70, 7760.0, 7798.99),
            nm.Bar(date(2026, 9, 25), 7750.0, 7700.0, 7743.41)]
    ath, line = nm.current_line(bars)
    assert ath == 7816.70
    assert round(line, 2) == 7035.03


def test_line_series_uses_prior_sessions_only() -> None:
    ls = nm.line_series(_bars([100, 110, 105]))
    assert [round(x.line, 2) for x in ls] == [90.0, 99.0]


def test_test_episode_anchor_is_lowest_low() -> None:
    closes = [100] * 5 + [91.5, 90.5, 95, 97]  # taps within 1% of the 90 line
    eps = nm.test_episodes(_bars(closes))
    assert len(eps) == 1
    assert eps[0].low == 90.5
    assert eps[0].anchor == _bars(closes)[6].d


def test_violation_is_weekly_close_below_line() -> None:
    # 2 flat weeks at 100, then a week that closes 85 on Friday
    closes = [100] * 10 + [95, 92, 90, 88, 85]
    bars = _bars(closes)
    v = nm.violations(bars, since=bars[0].d)
    assert len(v) == 1 and v[0][1] == 85


def test_bull_count_start_is_first_weekly_close_above_line() -> None:
    closes = [100] * 5 + [85] * 5 + [95] * 5
    bars = _bars(closes)
    assert nm.bull_count_start(bars, bull_start=bars[6].d) == bars[-1].d


def test_clock_projection_skips_weekends() -> None:
    d = nm.project_session_date(date(2026, 9, 25), 4, lambda x: x.weekday() < 5)
    assert d == date(2026, 10, 1)  # S124 on Fri 9/25 → S128 = Thu 10/1


def test_rail_divergence_needs_gap_and_lower_rail() -> None:
    dates = [b.d for b in _bars([1] * 30)]
    series = [(d, 10 + i) for i, d in enumerate(dates[:10])] + [(d, 5.0) for d in dates[10:]]
    rr = nm.rail_read("RSP", series, since=dates[0], price_high_date=dates[25],
                      session_dates=dates)
    assert rr.diverging and rr.gap_sessions == 16
    confirmed = nm.rail_read("RSP", series, since=dates[0], price_high_date=dates[12],
                             session_dates=dates)
    assert not confirmed.diverging  # peak only 3 sessions before the price high
