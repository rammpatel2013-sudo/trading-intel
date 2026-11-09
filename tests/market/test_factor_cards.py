"""Factor cards: z-score, zone entries, honest base rate (2026-11-09)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_intel.market import factor_cards as fc


def _prices(n: int = 2600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=n)
    cols = {}
    for s in fc.SYMBOLS:
        cols[s] = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.011, n)))
    return pd.DataFrame(cols, index=idx)


def test_rel_return_is_log_difference():
    idx = pd.bdate_range("2020-01-01", periods=60)
    a = pd.Series(np.linspace(100, 110, 60), idx)
    b = pd.Series(100.0, idx)
    r = fc.rel_return(a, b, 50)
    assert r.iloc[-1] == pytest.approx(100 * np.log(a.iloc[-1] / a.iloc[-51]))
    assert r.iloc[:50].isna().all()


def test_zscore_uses_only_past_window():
    x = pd.Series(np.r_[np.zeros(300), 10.0], index=pd.RangeIndex(301), dtype=float)
    x.iloc[:300] = np.random.default_rng(0).normal(0, 1, 300)
    z = fc.zscore(x, win=300, minp=252)
    assert z.iloc[:251].isna().all()
    assert z.iloc[-1] > 5


def test_entries_count_crossings_once_and_de_overlap():
    z = pd.Series([0, -2.5, -2.6, -1, -2.1, 0, 0, 0] + [0] * 20 + [-2.2, 0], dtype=float)
    got = fc.entries(z, "buy", gap=20)
    assert got == [1, 28]  # index 4 is a re-entry within 20 sessions -> skipped
    assert fc.entries(-z, "sell", gap=20) == [1, 28]


def test_base_rate_counts_and_grade():
    n = 400
    idx = pd.bdate_range("2015-01-01", periods=n)
    level = pd.Series(np.arange(n, dtype=float), idx)  # always rising -> 100% up both ways
    z = pd.Series(0.0, idx)
    for i in (50, 120, 200):
        z.iloc[i] = -3.0
    br = fc.base_rate(level, z, "buy")
    assert br["n"] == 3 and br["pct_up"] == 100.0 and br["pct_up_all"] == 100.0
    assert br["avg"] == pytest.approx(20.0)
    assert br["grade"] == "weak record"  # n < 30 and zero edge


def test_base_rate_stronger_record_when_edge_and_n():
    rng = np.random.default_rng(3)
    n = 6000
    idx = pd.bdate_range("2001-01-01", periods=n)
    steps = rng.normal(0, 1, n)
    z = pd.Series(0.0, idx)
    ent = list(range(100, n - 40, 120))  # 49 entries
    for i in ent:
        z.iloc[i] = -2.5
        steps[i + 1 : i + 21] = np.abs(steps[i + 1 : i + 21]) + 0.5  # always up after entry
    level = pd.Series(np.cumsum(steps), idx)
    br = fc.base_rate(level, z, "buy")
    assert br["n"] >= 30
    assert br["pct_up"] == 100.0
    assert br["edge_pts"] > 5
    assert br["grade"] == "stronger record"


@pytest.mark.parametrize(
    "z,zone",
    [
        (-2.3, "buy"),
        (2.0, "sell"),
        (-1.6, "nearing_buy"),
        (1.7, "nearing_sell"),
        (0.4, "none"),
        (None, "none"),
    ],
)
def test_zone_of(z, zone):
    assert fc.zone_of(z) == zone


def test_build_cards_shapes_and_disclaimer():
    out = fc.build_cards(_prices())
    assert len(out["cards"]) == 6
    for c in out["cards"]:
        assert c["found"], c
        assert np.isfinite(c["z"]) and c["z_2w"] is not None
        assert c["record"]["grade"] in ("weak record", "stronger record")
        assert "next 20 sessions" in c["sentence"] or "no record" in c["sentence"]
        assert len(c["spark"]) == len(c["spark_dates"]) <= fc.SPARK
    assert out["note"] == fc.DISCLAIMER
    rates = next(c for c in out["cards"] if c["key"] == "rates")
    assert rates["kind"] == "beta"


def test_short_history_card_says_so():
    px = _prices(n=300)
    c = fc.card(fc.FACTORS[0], px)
    assert not c["found"] and c["reason"] == "history too short"
    c2 = fc.card(fc.FACTORS[0], _prices(n=1200))
    assert c2["found"] and c2["short_history"]


def test_missing_symbol():
    c = fc.card(fc.FACTORS[0], _prices().drop(columns=["SPHB"]))
    assert c == {"key": "beta", "name": "Beta", "found": False, "reason": "missing prices"}


def test_vol_factor_sign_low_vol_beating_market_is_down():
    idx = pd.bdate_range("2015-01-01", periods=60)
    usmv = pd.Series(np.linspace(100, 110, 60), idx)
    spy = pd.Series(100.0, idx)
    assert fc.rel_return(spy, usmv).iloc[-1] < 0  # long SPY short USMV -> "vol factor down"
