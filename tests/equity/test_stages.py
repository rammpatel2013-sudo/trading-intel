"""Stage rules, rotation and stage breadth (2026-11-22). Pure numpy, no DB."""

from datetime import date, timedelta

import numpy as np

from trading_intel.equity import rotation as rot
from trading_intel.equity import stage_breadth as sb
from trading_intel.equity import stages as st


def _bars(path: np.ndarray, noise: float = 0.002, seed: int = 1):
    rng = np.random.default_rng(seed)
    c = path * (1 + rng.normal(0, noise, len(path)))
    return c, c * 1.01, c * 0.99


def test_trend_stages():
    t = np.arange(300)
    up = 50 * np.exp(0.004 * t)
    dn = 50 * np.exp(-0.004 * t)
    flat = 50 + 0.05 * np.sin(t / 3)
    cols = [_bars(x, seed=i) for i, x in enumerate((up, dn, flat))]
    C = np.column_stack([c[0] for c in cols])
    H = np.column_stack([c[1] for c in cols])
    L = np.column_stack([c[2] for c in cols])
    code, xatr = st.classify(C, H, L)
    main = st.main_stage(code)
    assert (code[:49] == 0).all()                         # no stage before the 50-day average exists
    assert (main[-60:, 0] == 2).mean() > 0.6              # steady rise -> mostly Stage 2
    assert (main[-60:, 1] == 4).mean() > 0.6              # steady fall -> mostly Stage 4
    assert (main[-60:, 2] == 1).mean() > 0.25 and (main[-60:, 2] == 2).mean() < 0.5   # flat -> basing often, no trend
    assert np.nanmean(xatr[-60:, 0]) > 0 > np.nanmean(xatr[-60:, 1])


def test_extended_codes_and_breakout():
    t = np.arange(260)
    px = np.where(t < 200, 50.0, 50 * np.exp(0.03 * (t - 200)))   # a base, then a straight-up run
    c, h, lo = _bars(px, noise=0.0005)
    code, _ = st.classify(c[:, None], h[:, None], lo[:, None])
    names = {st.CODES[k] for k in code[200:, 0]}
    assert "2B" in names and ({"2C", "2D"} & names)


def test_ema_sma_match_simple_cases():
    x = np.arange(1.0, 61.0)[:, None]
    assert abs(st.sma(x, 50)[-1, 0] - np.mean(np.arange(11.0, 61.0))) < 1e-12
    e = st.ema(np.ones((30, 1)) * 5, 10)
    assert np.allclose(e[~np.isnan(e)], 5)


def test_rotation_quadrants():
    n = 400
    spy = 100 * np.exp(0.0003 * np.arange(n))
    lead = spy * np.exp(0.002 * np.arange(n))                  # keeps beating SPY
    lag = spy * np.exp(-0.002 * np.arange(n))
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(n)]
    out = {r["symbol"]: r for r in rot.rotation(days, {"SPY": spy, "XLK": lead, "XLU": lag})}
    assert out["XLK"]["ratio"] > 100 and out["XLU"]["ratio"] < 100
    assert out["XLK"]["quadrant"] in ("Leading", "Weakening") and out["XLU"]["quadrant"] in ("Lagging", "Improving")
    assert len(out["XLK"]["tail"]) == 6


def test_breadth_compute_shapes():
    rng = np.random.default_rng(3)
    T, N = 420, 40
    drift = rng.normal(0, 0.003, N)
    C = 50 * np.exp(np.cumsum(rng.normal(0, 0.012, (T, N)) + drift, axis=0))
    H, L = C * 1.01, C * 0.99
    member = np.ones((T, N), bool)
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(T)]
    spy = 400 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, T)))
    o = sb.compute(days, [f"S{i}" for i in range(N)], C, H, L, member, ["Technology"] * N, None, {"SPY": spy})
    ids = [c["id"] for c in o["cards"]]
    assert ids == ["STAGE2", "STAGE4", "NET5", "EXT", "WOE", "WS2", "WS4"]     # no industry list -> no INDPOS
    last = [o["mix"][k][-1] for k in ("s1", "s2", "s3", "s4")]
    assert 0 < sum(last) <= 100.01
    assert o["sectors"] and o["sectors"][0]["etf"] == "XLK" and o["n_members"] == N
    assert o["cards"][0]["year"]["vals"] and o["cards"][0]["year"]["rank"] is not None
    rows = sb.stage_rows(days, [f"S{i}" for i in range(N)], *st.classify(C, H, L), member, since=days[-3])
    assert {r["date"] for r in rows} == set(days[-3:])



def test_weekly_stage_and_groups():
    from trading_intel.equity import stage_groups as sg

    days = [d for d in (date(2023, 1, 2) + timedelta(days=i) for i in range(700)) if d.weekday() < 5][:480]
    t = np.arange(len(days))
    up, dn = 50 * np.exp(0.002 * t), 50 * np.exp(-0.002 * t)
    C = np.column_stack([up, dn] * 4)
    ends = sg.week_ends(days)
    assert all(days[i].weekday() == 4 for i in ends[:-1])            # Fridays (no holidays in this calendar)
    ws = sg.weekly_stage(C[ends])
    assert (ws[:33] == 0).all() and (ws[-10:, 0] == 2).all() and (ws[-10:, 1] == 4).all()
    member = np.ones(C.shape, dtype=bool)
    blk, g = sg.weekly(days, list("ABCDEFGH"), C, member, 50 * np.exp(0.001 * t), ["s"] * 8, None)
    assert blk["mix"]["s2"][-1] == 50.0 and blk["mix"]["s4"][-1] == 50.0
    assert np.isnan(g["WS2"]).all()                                   # under 20 members -> no gauge
    main = np.tile(np.array([2, 4] * 4), (len(days), 1))
    ind = ["Rising group", "Falling group"] * 4                       # each group: four of one kind
    rows, pos = sg.industries(days, ind, ["s"] * 8, main, member)
    assert [r["name"] for r in rows] == ["Rising group", "Falling group"]
    assert rows[0]["s2"] == 100.0 and rows[1]["s4"] == 100.0 and np.isnan(pos[-1])   # under 10 groups -> no gauge
