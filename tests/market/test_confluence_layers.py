"""Confluence layers (b)-(e): OI clusters, VIX mapping, price memory, crowded strikes."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from trading_intel.market import confluence_layers as cl
from trading_intel.market.confluence_layers import Bar, BigTradeLeg, ChainRow, SmilePoint, VixOption

D = date(2026, 11, 6)


def test_oi_levels_aggregate_across_expiries_with_oi_change() -> None:
    chain = [ChainRow(7000.0, "P", 14, 0.2, 100_000, 3000), ChainRow(7000.0, "P", 42, 0.2, 50_000, 2000),
             ChainRow(7200.0, "P", 5, 0.2, 10_000, -500), ChainRow(7650.0, "C", 5, 0.2, 12_000, None),
             ChainRow(5000.0, "P", 14, 0.2, 900_000, 0)]  # far wing: outside the band
    lv, gaps = cl.oi_levels(chain, spot=7376.86)
    top = next(x for x in lv if x["kind"] == "put_oi" and x["bucket"] == "<=45d")
    assert top["level"] == 7000.0 and top["oi"] == 150_000 and top["oi_change"] == 5000
    assert all(x["level"] != 5000.0 for x in lv)
    front = [x for x in lv if x["bucket"] == "<=7d"]
    assert {x["level"] for x in front} == {7200.0, 7650.0}
    assert next(x for x in front if x["level"] == 7650.0)["oi_change"] is None
    assert gaps == []


def test_oi_levels_empty_chain_reports_gap() -> None:
    lv, gaps = cl.oi_levels([], spot=7000.0)
    assert lv == [] and "OI clusters skipped" in gaps[0]


def test_vix_mapping_arithmetic() -> None:
    # VIX 25.64 -> 30 is +4.36 pts; at 2.0 pts per 1% that is a 2.18% SPX drop
    assert cl.vix_to_spx(30, 25.64, 7376.86, 2.0) == pytest.approx(7376.86 * (1 - 0.0218))
    # VIX 18 put strike -> 3.82% rally
    assert cl.vix_to_spx(18, 25.64, 7376.86, 2.0) == pytest.approx(7376.86 * 1.0382)
    assert cl.vix_to_spx(25.64, 25.64, 7000, 2.0) == 7000


def test_vix_levels_pick_biggest_oi_and_skip_thin_expiries() -> None:
    e1, e2, thin = date(2026, 11, 18), date(2026, 12, 16), date(2026, 11, 11)
    opts = [VixOption(e1, 30, "call", 338_000), VixOption(e1, 25, "call", 305_000),
            VixOption(e1, 20, "call", 1000), VixOption(e1, 18, "put", 361_000),
            VixOption(e1, 17, "put", 292_000), VixOption(e2, 30, "call", 184_000),
            VixOption(e2, 17, "put", 221_000), VixOption(thin, 22, "call", 100)]
    lv, gaps = cl.vix_levels(opts, as_of=D, vix_spot=25.64, spot=7376.86, beta_measured=3.0)
    strikes = {(x["bucket"], x["vix_strike"]) for x in lv}
    assert ("2026-11-18", 30) in strikes and ("2026-11-18", 25) in strikes
    assert ("2026-11-18", 20) not in strikes and all(b != "2026-11-11" for b, _ in strikes)
    assert any("2026-11-11 skipped" in g for g in gaps)
    x30 = next(x for x in lv if x["bucket"] == "2026-11-18" and x["vix_strike"] == 30)
    assert x30["level"] == pytest.approx(7216.0, abs=0.2)
    assert x30["level_measured_beta"] == pytest.approx(cl.vix_to_spx(30, 25.64, 7376.86, 3.0), abs=0.1)


def test_vix_levels_without_vix_close() -> None:
    lv, gaps = cl.vix_levels([], as_of=D, vix_spot=None, spot=7000)
    assert lv == [] and "no VIX close" in gaps[0]


def test_measured_beta_points() -> None:
    days = [D - timedelta(days=i) for i in range(30)][::-1]
    spx, vix, s, v = [], [], 7000.0, 20.0
    for i, d in enumerate(days):
        r = 0.01 if i % 2 else -0.01
        s *= 1 + r
        v *= 1 - 5 * r  # VIX moves 5x the SPX % move, opposite sign
        spx.append((d, s))
        vix.append((d, v))
    pts = cl.measured_beta_pts(spx, vix)
    assert pts == pytest.approx(5 * vix[-1][1] / 100, rel=0.02)
    assert cl.measured_beta_pts(spx[:5], vix[:5]) is None


def _bars(n: int, start: date = date(2026, 1, 2)) -> list[Bar]:
    out, d = [], start
    for i in range(n):
        c = 100.0 + i
        out.append(Bar(d, c, c + 1, c - 1, c, 1000.0))
        d += timedelta(days=1)
    return out


def test_sma_and_short_history_gap() -> None:
    bars = _bars(60)
    lv, gaps = cl.price_levels(bars, as_of=bars[-1].d, spot=bars[-1].close, band=10)
    smas = {x["label"]: x["level"] for x in lv if x["kind"] == "sma"}
    assert smas["SMA20"] == pytest.approx(sum(100.0 + i for i in range(40, 60)) / 20)
    assert "SMA100" not in smas and any("SMA100 needs 100" in g for g in gaps)
    assert any("anchored VWAP omitted" in g for g in gaps)  # no SPY bars passed


def test_swing_pivots_five_bar_window() -> None:
    bars = _bars(30)
    hi = bars[15]
    bars[15] = Bar(hi.d, hi.open, 500.0, hi.low, hi.close)
    piv = cl.swing_pivots(bars, k=5)
    assert ("high", bars[15]) in piv
    assert not any(b.d == bars[2].d for _, b in piv)  # within k of the start: not judged


def test_prior_month_and_anchored_vwap_via_spy() -> None:
    spx = [Bar(date(2026, 10, d), 7000, 7100 + d, 6900 - d, 7000) for d in range(1, 31)]
    spx += [Bar(date(2026, 11, 2), 7000, 7050, 6950, 7000)]
    spy = [Bar(b.d, 700, 710, 690, 700, 1000.0) for b in spx]
    lv, _ = cl.price_levels(spx, as_of=date(2026, 11, 2), spot=7000, spy=spy, band=1)
    pm = {x["kind"]: x["level"] for x in lv if x["kind"].startswith("prior_month")}
    assert pm == {"prior_month_low": 6870.0, "prior_month_high": 7130.0}
    assert cl.anchored_vwap(spy, spy[0].d) == pytest.approx(700.0)


def test_third_friday_and_next_monthly() -> None:
    assert cl.third_friday(2026, 11) == date(2026, 11, 20)
    assert cl.next_monthly_dte(D) == 14
    assert cl.next_monthly_dte(date(2026, 11, 16)) == (date(2026, 12, 18) - date(2026, 11, 16)).days


def test_crowd_levels_delta_strikes_and_trades() -> None:
    smile = [SmilePoint(7, d, side, 0.26 if side == "put" else 0.19, 7378.9)
             for d in (20.0, 25.0, 30.0) for side in ("put", "call")]
    trades = [BigTradeLeg(D, "a", 7000.0, "P", date(2027, 1, 15), 25e6),
              BigTradeLeg(D, "a", 7350.0, "P", date(2027, 3, 19), 76e6),
              BigTradeLeg(D, "b", 8000.0, "P", date(2026, 12, 18), 119e6, financing=True)]
    lv, gaps = cl.crowd_levels(smile, trades, as_of=D, spot=7376.86)
    p25 = next(x for x in lv if x["kind"] == "25d_put")
    assert 7100 < p25["level"] < 7300
    p22 = next(x for x in lv if x["kind"] == "22d_put")
    assert p22["level"] < p25["level"]  # further OTM
    bt = [x for x in lv if x["kind"] == "big_trade"]
    assert {x["level"] for x in bt} == {7000.0, 7350.0}
    assert any("financing" in g for g in gaps)


def test_crowd_levels_no_inputs() -> None:
    lv, gaps = cl.crowd_levels([], [], as_of=D, spot=7000)
    assert lv == [] and len(gaps) == 2
