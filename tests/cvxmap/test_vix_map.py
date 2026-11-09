# ruff: noqa: N806, N812  (house style: L / W / V / X module aliases, F / K / T as in the maths)
"""vix_map (2026-11-09): VIX dealer gamma / vanna map maths - Black-76 greeks vs finite differences, the dealer
sign, the zero cross, and a column built from a synthetic VIX pull (no NAS data needed)."""
import datetime
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "cvxmap"))
import cvx_collect as C
import cvx_live as L
import vix_map as V


@pytest.mark.parametrize("f,k,t,s", [(18.0, 20.0, 14 / 365, 0.95), (18.0, 15.0, 7 / 365, 1.2),
                                     (25.0, 25.0, 30 / 365, 0.8), (14.5, 22.0, 63 / 365, 1.05)])
def test_black76_gamma_and_vanna_match_finite_differences(f, k, t, s):
    h, e = 1e-3 * f, 1e-4
    for kind in ("C", "P"):
        fd_gamma = (V.b76_price(f + h, k, t, s, kind) - 2 * V.b76_price(f, k, t, s, kind)
                    + V.b76_price(f - h, k, t, s, kind)) / (h * h)
        assert V.b76_gamma(f, k, t, s) == pytest.approx(fd_gamma, rel=2e-4, abs=1e-9)
        hd = 1e-5 * f
        fd_delta = (V.b76_price(f + hd, k, t, s, kind) - V.b76_price(f - hd, k, t, s, kind)) / (2 * hd)
        assert V.b76_delta(f, k, t, s, kind) == pytest.approx(fd_delta, rel=1e-5, abs=1e-9)
        fd_vanna = (V.b76_delta(f, k, t, s + e, kind) - V.b76_delta(f, k, t, s - e, kind)) / (2 * e)
        assert V.b76_vanna(f, k, t, s) == pytest.approx(fd_vanna, rel=1e-5, abs=1e-9)


def test_put_call_parity_r0():
    f, k, t, s = 19.0, 21.0, 20 / 365, 1.0
    assert V.b76_price(f, k, t, s, "C") - V.b76_price(f, k, t, s, "P") == pytest.approx(f - k, abs=1e-12)


def test_strike_iv_uses_out_of_the_money_side():
    assert V.strike_iv(25, 18, 1.1, 0.9) == 1.1      # call side above the forward
    assert V.strike_iv(15, 18, 1.1, 0.9) == 0.9      # put side below
    assert V.strike_iv(25, 18, 0.0, 0.9) == 0.9      # fallback


def test_dealer_sign_default_is_short_calls_long_puts_and_naive_is_the_negative():
    levels = [14.0, 16.0, 18.0, 20.0, 22.0]
    calls_only = [{"F": 18.0, "T": 14 / 365, "rows": [(20.0, 1000, 0, 0, 0, 1.0, 1.0)]}]
    puts_only = [{"F": 18.0, "T": 14 / 365, "rows": [(16.0, 0, 1000, 0, 0, 1.0, 1.0)]}]
    g, _ = V.dealer_profile(calls_only, levels, 18.0)
    assert all(x < 0 for x in g)                      # dealers short calls -> short gamma
    g, _ = V.dealer_profile(puts_only, levels, 18.0)
    assert all(x > 0 for x in g)                      # dealers long puts -> long gamma
    both = [{"F": 18.0, "T": 14 / 365, "rows": [(20.0, 900, 300, 50, 70, 1.1, 1.0), (16.0, 200, 800, 30, 90, 1.0, 0.9)]}]
    gd, vd = V.dealer_profile(both, levels, 18.0, sign=V.SIGN_DEALER)
    gn, vn = V.dealer_profile(both, levels, 18.0, sign=V.SIGN_NAIVE)
    assert all(a == pytest.approx(-b) for a, b in zip(gd, gn, strict=True))
    assert all(a == pytest.approx(-b) for a, b in zip(vd, vn, strict=True))


def test_dealer_profile_units_and_forward_shift():
    # one call strike, OI 1000: gamma $k = -1000 * 100 * gamma / 1000 at the shifted forward
    ex = [{"F": 19.0, "T": 21 / 365, "rows": [(22.0, 1000, 0, 0, 0, 0.9, 0.9)]}]
    g, v = V.dealer_profile(ex, [17.0, 20.0], 17.0)
    assert g[0] == pytest.approx(-100 * V.b76_gamma(19.0, 22.0, 21 / 365, 0.9))
    assert g[1] == pytest.approx(-100 * V.b76_gamma(22.0, 22.0, 21 / 365, 0.9))   # VIX +3 -> F +3
    assert v[1] == pytest.approx(-100 * V.b76_vanna(22.0, 22.0, 21 / 365, 0.9) * 0.01)
    gv, _ = V.dealer_profile([{"F": 19.0, "T": 21 / 365, "rows": [(22.0, 1000, 0, 400, 0, 0.9, 0.9)]}], [17.0], 17.0, "vol")
    assert gv[0] == pytest.approx(0.4 * g[0])


def test_zero_cross_linear_and_nearest():
    lv = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    assert V.zero_cross(lv, [2, 1, -1, -2, -3, -4], 13.0) == pytest.approx(11.5)
    assert V.zero_cross(lv, [-1, 1, 1, 1, -1, -1], 14.6) == pytest.approx(13.5)   # nearest of two
    assert V.zero_cross(lv, [1, 2, 3, 4, 5, 6], 12.0) is None
    # a real profile: puts low, calls high -> gamma flips from + (below) to - (above)
    ex = [{"F": 18.0, "T": 14 / 365, "rows": [(15.0, 0, 5000, 0, 0, 0.9, 0.9), (25.0, 5000, 0, 0, 0, 1.2, 1.2)]}]
    lv = [10 + 0.25 * i for i in range(80)]
    g, _ = V.dealer_profile(ex, lv, 17.0)
    z = V.zero_cross(lv, g, 17.0)
    assert z is not None and 13.0 < z < 24.0 and g[0] > 0 and g[-1] < 0


def test_profiles_equal_dealer_profile():
    ex = [{"F": 18.4, "T": 12 / 365, "rows": [(16.0, 300, 9000, 50, 400, 0.8, 0.85), (25.0, 20000, 100, 900, 20, 1.2, 1.1)]},
          {"F": 19.1, "T": 40 / 365, "rows": [(15.0, 100, 4000, 0, 70, 0.7, 0.8), (30.0, 9000, 0, 300, 0, 1.0, 1.0)]}]
    lv = V.grid_for(17.5)
    P = V.profiles(ex, lv, 17.5)
    for book, sel in (("f", ex[:1]), ("a", ex)):
        for b, basis in (("o", "oi"), ("v", "vol")):
            g, v = V.dealer_profile(sel, lv, 17.5, basis)
            assert P["g" + book + b] == pytest.approx(g, rel=1e-12, abs=1e-12)
            assert P["v" + book + b] == pytest.approx(v, rel=1e-12, abs=1e-12)


def test_grid():
    g = V.grid_for(17.2)
    assert g[0] == 10.0 and g[-1] == 33.0 and g[1] - g[0] == pytest.approx(0.25)
    assert V.grid_for(9.0)[0] == 8.0


def make_vix_rec(ts="2026-11-06T10:35:00", vix=17.2):
    """One fake 'vix' pull: three Wednesday expiries, strikes 10..40."""
    now = datetime.datetime.fromisoformat(ts)
    params = list(C.PARAMS)
    eff = [p for p in params if p not in L.DROPPED]
    chain, d = [], now.date()
    while len(chain) < 3:
        d += datetime.timedelta(days=1)
        if d.weekday() != 2:
            continue
        T = (datetime.datetime(d.year, d.month, d.day, 9, 30) - now).total_seconds() / 60 / 525600
        F = vix + 0.6 * (len(chain) + 1)
        rows = []
        for k in [10 + 0.5 * i for i in range(61)]:
            pair = []
            for cp in "CP":
                sig = 0.9 + 0.3 * abs(math.log(k / F))
                px = V.b76_price(F, k, T, sig, cp)
                v = {p: 0.0 for p in eff}
                oi = 50000 * math.exp(-abs(k - 25) / 3) if cp == "C" else 40000 * math.exp(-abs(k - 15) / 1.5)
                v.update(bid_price=max(px - 0.02, 0.0), ask_price=px + 0.02, price=px, oi=oi, volm=oi / 20,
                         volatility=sig)
                pair.append([f".VIX{d:%y%m%d}{cp}{k}", *[v[p] for p in eff]])
            rows.append([k, *pair])
        chain.append([(d - datetime.date(1970, 1, 1)).days, rows])
    return {"ts": ts, "pull": "vix", "symbols": ["VIX"], "exps": list(range(10)), "rng": 5.0,
            "params": params, "data": [{"spot": vix, "chain": chain}]}


def test_column_from_a_vix_pull():
    rec = make_vix_rec()
    now = datetime.datetime.fromisoformat(rec["ts"])
    dv = L.decode(rec)["VIX"]
    lv = V.grid_for(17.2)
    col = V.column(dv, now, lv)
    assert col["ne"] == 3 and col["x"] == 17.2 and col["fF"] == pytest.approx(17.8, abs=0.05)
    assert col["top"]["front"] == {"call": 25.0, "put": 15.0}
    for key in ("gfo", "gfv", "gao", "gav", "vfo", "vfv", "vao", "vav"):
        assert len(col[key]) == len(lv)
    # puts below, calls above -> dealers long gamma low, short gamma high; one zero cross between
    z = V.zero_cross(lv, col["gao"], 17.2)
    assert col["gao"][0] > 0 and col["gao"][-1] < 0 and z is not None
    vd = V.VixDay("20261106")
    assert vd.feed(dv, now) and vd.payload()["cols"][0]["t"] == "10:35"
    assert not vd.feed(dv, datetime.datetime(2026, 11, 6, 9, 10))     # before the open: ignored
