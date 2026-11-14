"""E2-E6 maths: factor model recovers planted factor returns; damage, gate, candidates, cards, risk."""

from datetime import date, timedelta

import numpy as np

from trading_intel.equity import candidates as cand
from trading_intel.equity import damage, gate, nhnl, risk, trade_card
from trading_intel.equity.factor_model import covariance, fit_day
from trading_intel.equity.panel import Panel
from trading_intel.equity.styles import STYLES, standardize

from .synth import END, World


def test_fit_day_recovers_planted_returns():
    rng = np.random.default_rng(1)
    n, k = 400, len(STYLES)
    z = rng.normal(size=(n, k))
    sectors = [("A", "B", "C")[i % 3] for i in range(n)]
    cap = rng.uniform(1e9, 5e10, n)
    f_sty = np.linspace(-0.004, 0.004, k)
    f_sec = {"A": 0.003, "B": -0.001}
    c = np.array([cap[[s == x for s in sectors]].sum() for x in "ABC"]) / cap.sum()
    f_sec["C"] = -(c[0] * f_sec["A"] + c[1] * f_sec["B"]) / c[2]
    r = 0.01 + np.array([f_sec[s] for s in sectors]) + z @ f_sty + rng.normal(0, 1e-4, n)
    fit = fit_day(r, cap, sectors, z, np.ones(n, bool))
    assert abs(fit.factors["market"] - 0.01) < 2e-4
    assert abs(fit.factors["sector:A"] - 0.003) < 2e-4
    for j, s in enumerate(STYLES):
        assert abs(fit.factors[s] - f_sty[j]) < 3e-4  # returns are clipped at 1-99% for the fit
    assert fit.r2 > 0.95 and np.nanstd(fit.resid) < 5e-4


def test_standardize_cap_weighted_mean_zero():
    rng = np.random.default_rng(2)
    raw = rng.normal(size=(3, 200))
    w = rng.uniform(1, 10, size=(3, 200))
    z = standardize(raw, w, np.ones((3, 200), bool))
    assert abs(np.average(z[1], weights=w[1])) < 1e-9 and abs(np.std(z[1]) - 1) < 1e-9


def _panel(world: World) -> Panel:
    syms = [s for s in world.all if s not in ("SPY", "RSP", "QQQ", "IWM", "MDY", "IJR")] + [
        "SPY",
        "RSP",
    ]
    close = np.column_stack([world.close[s] for s in syms])
    member = np.ones_like(close, dtype=bool)
    member[:, -2:] = False
    vol = np.column_stack([world.vol[s] for s in syms])
    return Panel(
        world.dates,
        syms,
        close,
        close * 1.01,
        close * 0.99,
        vol,
        close * 1e8,
        member,
        [world.sector[s] for s in syms],
    )


def test_damage_and_ma_breadth_shapes():
    p = _panel(World(days=400))
    st = damage.damage_stats(p)
    t = len(p.dates) - 1
    assert -100 <= st["median_drawdown"][t] <= 0 and 0 <= st["pct_down_20"][t] <= 100
    assert np.isfinite(st["repair_flow"][t]) and abs(np.nansum(st["bucket_shares"][t]) - 100) < 1e-6
    assert 0 <= damage.pct_above_ma(p, 50)[t] <= 100
    assert np.isfinite(damage.hidden_bear_gap(p)[t])
    b = damage.buckets(np.array([[0.0, -0.15, -0.3, -0.6, np.nan]]))
    assert b.tolist() == [[0, 1, 2, 3, -1]]


def test_nhnl_guards_and_ten_day():
    raw = [
        {
            "date": (date(2026, 10, 1) + timedelta(days=i)).isoformat(),
            "total_issues": 4700,
            "nyse_new_highs": 50,
            "nyse_new_lows": 10,
            "nyse_issues": 2000,
            "nasdaq_new_highs": 30,
            "nasdaq_new_lows": 20,
            "nasdaq_issues": 2500,
        }
        for i in range(30)
    ]
    raw[-1]["total_issues"] = 4000  # partial vendor run
    rows = nhnl.parse_rows(raw)
    assert rows[-1]["status"] == "suspect" and rows[-2]["status"] == "ok"
    v, st = nhnl.ten_day(rows, rows[-2]["date"])
    assert st == "ok" and abs(v - 50 / 4500 * 100) < 1e-9
    assert nhnl.ten_day(rows, rows[-1]["date"]) == (None, "suspect")
    assert nhnl.ten_day(rows, date(2027, 1, 1))[1] == "not_collected"


def test_gate_votes_and_colors():
    healthy = {
        "median_drawdown": -6,
        "repair_flow": 9,
        "nhnl_10d": 2,
        "pct_above_50": 70,
        "pct_above_200": 72,
        "spy_above_50": True,
        "spy_above_200": True,
        "q25_ratio": 2.0,
        "beta_reset_label": "RESET",
    }
    g = gate.decide(healthy, score_5d_ago=2)
    assert g["color"] == "green" and g["score"] == 8 and g["trend"] == "improving"
    sick = {
        "median_drawdown": -30,
        "repair_flow": -12,
        "pct_above_50": 20,
        "spy_above_200": False,
        "spy_above_50": False,
    }
    assert gate.decide(sick)["color"] == "red"
    assert gate.decide({"median_drawdown": -15})["color"] == "amber"
    assert "GREEN" in gate.headline(g)


def test_trend_box_and_candidates():
    w = World(days=650)
    c = np.linspace(50, 120, 300)
    ok, info = cand.trend_ok(c)
    assert ok and info["sma50"] > info["sma200"]
    flat = np.full(200, 10.0)
    assert cand.box(flat * 1.01, flat * 0.99, 199)[0] == 180
    p = _panel(w)
    from trading_intel.equity.factor_model import run_model

    run = run_model(p)
    rows = cand.build(p, run)
    names = {r["list_name"] for r in rows}
    assert "leader" in names and any(
        r["symbol"] == w.all[10] for r in rows if r["list_name"] == "breakout"
    )
    assert set(cand.shortlist(rows)) >= {w.all[10]}


def test_trade_card_and_risk():
    w = World(days=300)
    sym = w.all[0]
    ch = w.chain(sym)
    sp = trade_card.debit_spread(ch, END)
    assert (
        sp
        and sp["dte"] == 120
        and sp["long_strike"] < sp["short_strike"]
        and 0 < sp["debit"] < sp["width"]
    )
    assert sp["max_loss"] == round(sp["debit"] * 100, 0)
    row = trade_card.call_iv_row(ch, sym, END)
    assert row["dte"] == 120 and abs(row["iv_call_25d"] - 0.30) < 1e-9
    hist = [{"iv_call_25d": 0.30 + 0.003 * i, "spot": 100 + 0.1 * i} for i in range(11)]
    assert trade_card.call_bid(hist)["call_bid"] is True
    assert trade_card.call_bid(hist[:3])["ready"] is False
    st = risk.stops(100.0, 2.0, 97.0)
    assert st["atr_stop"] == 95.0 and st["ma50_stop"] == 95.06 and st["stop"] == 95.06
    sz = risk.position_size(100.0, 95.0)
    assert sz["shares"] == 100 and sz["risk_usd"] == 500
    assert risk.spread_contracts(2.5) == 2


def test_book_risk_flags_momentum_bet():
    names = ["market", "sector:Tech", "momentum", "beta"]
    rng = np.random.default_rng(3)
    rows = [
        {
            "date": date(2026, 1, 1) + timedelta(days=i),
            "factor": f,
            "ret": float(rng.normal(0, 0.01)),
        }
        for i in range(200)
        for f in names
    ]
    cov = covariance(rows, names)
    expo = {
        "A": {"momentum": 1.5, "beta": 0.5, "sector": "Tech"},
        "B": {"momentum": 1.2, "beta": 0.2, "sector": "Tech"},
    }
    out = risk.book_risk({"A": 0.5, "B": 0.5}, expo, cov, names, {"A": 1e-4, "B": 1e-4})
    assert out["style_exposure"]["momentum"] == 1.35 and out["predicted_vol_pct"] > 0
    assert any("momentum" in f for f in out["flags"]) and any("Tech" in f for f in out["flags"])
