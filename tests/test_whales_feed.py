"""tests for scripts/whales_feed.py (2026-11-13, #97 step 2)."""
import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path

_P = Path(__file__).resolve().parents[1] / "scripts" / "whales_feed.py"
_spec = importlib.util.spec_from_file_location("whales_feed", _P)
wf = importlib.util.module_from_spec(_spec)
sys.modules["whales_feed"] = wf
_spec.loader.exec_module(wf)

D = date(2026, 11, 16)


def row(h, m, bucket, sd, exp=D, spot=6000.0):
    return {"minute": datetime(2026, 11, 16, h, m), "expiry": exp, "bucket": bucket, "signed_delta": sd, "spot": spot}


def test_cumulative_and_scopes():
    rows = [row(9, 30, "whale", 1000), row(9, 31, "crowd", -500), row(9, 31, "whale", 200, exp=date(2026, 11, 20))]
    out = wf.build(rows, D, 9 * 60 + 32, 20)
    assert out["ok"] and out["n"] == 2
    assert out["series"]["0dte"]["whale"] == [6.0, 6.0]           # 1000 x 6000 = $6M
    assert out["series"]["all"]["whale"] == [6.0, 7.2]            # + the Friday expiry
    assert out["series"]["0dte"]["crowd"] == [0.0, -3.0]
    assert out["totals"]["all"]["crowd"] == -3.0


def test_outside_session_dropped_and_spot_carried():
    rows = [row(9, 29, "whale", 1000), row(9, 30, "whale", 100), row(9, 33, "crowd", 100, spot=None)]
    flows, spot = wf.minute_flows(rows, D)
    assert 9 * 60 + 29 not in flows["0dte"]["whale"]
    assert spot[9 * 60 + 33] == 6000.0
    assert flows["0dte"]["crowd"][9 * 60 + 33] == 600000.0


def test_opposed_windows():
    whale = {9 * 60 + 30 + i: 1e6 for i in range(5)}                # +$5M in 09:30-09:35
    crowd = {9 * 60 + 30 + i: -1e6 for i in range(5)}               # -$5M
    for t0 in range(9 * 60 + 35, 10 * 60 + 30, 5):                  # other windows small, same sign
        whale[t0] = 2e5
        crowd[t0] = 2e5
    ops = wf.opposed(whale, crowd, 10 * 60 + 29)
    assert len(ops) == 1 and ops[0]["t0"] == "09:30" and ops[0]["whales_side"] == "up"


def test_no_rows():
    out = wf.build([], D, 600, 20)
    assert out["ok"] is False


def test_day_record_and_score():
    live = {"date": "2026-11-16", "spot": [6000.0 + i for i in range(120)],
            "opp": {"0dte": [{"t0": "09:30", "t1": "09:35", "whale": 5, "crowd": -5, "whales_side": "up"}], "all": []}}
    rec = wf.day_record(live)
    w = rec["windows"]["0dte"][0]
    assert w["spx_end"] == 6005.0 and w["spx_fwd"] == 6035.0
    sc = wf.score([rec])
    assert sc["scopes"]["0dte"]["whales_right"] == 1 and "collecting" in sc["verdict"]
    sc2 = wf.score([rec] * 20)
    assert sc2["verdict"].startswith("whales lead")


# ---------------------------------------------------------------- 2026-11-21 VIX (C3): lots, not $ delta
def vrow(h, m, bucket, buy, sell, other=0.0, sd=0.0, spot=20.0, day=D):
    return {"minute": datetime(day.year, day.month, day.day, h, m), "bucket": bucket, "buy": buy, "sell": sell,
            "other": other, "sd": sd, "spot": spot}


def test_vix_build_lots_pre_and_volume():
    prev = date(2026, 11, 13)
    rows = [vrow(20, 30, "whale", 40, 0, day=prev), vrow(8, 0, "crowd", 3, 10),          # overnight + pre-market
            vrow(9, 30, "whale", 50, 10, 5), vrow(9, 30, "crowd", 4, 9),
            vrow(9, 31, "crowd", 2, 8, spot=20.4), vrow(9, 32, "whale", 0, 30, spot=20.1)]
    out = wf.vix_build(rows, D, 9 * 60 + 40, 20)
    assert out["ok"] and out["units"] == "contracts" and out["n"] == 3      # 09:30..09:32 (last print 09:32)
    assert out["series"]["whale"] == [40, 40, 10]
    assert out["series"]["crowd"] == [-5, -11, -11]
    assert out["vol"]["tot"] == [78, 10, 30] and out["vol"]["buy"] == [54, 2, 0] and out["vol"]["sell"] == [19, 8, 30]
    assert out["spot"] == [20.0, 20.4, 20.1]
    assert out["pre"] == {"whale": 40, "crowd": -7, "tot": 53, "from": "11-13 20:30"}
    assert out["totals"] == {"whale": 10, "crowd": -11}


def test_vix_build_empty_and_pre_only():
    assert not wf.vix_build([], D, 600, 20)["ok"]
    out = wf.vix_build([vrow(8, 0, "crowd", 3, 1)], D, 600, 20)
    assert not out["ok"] and out["pre"]["crowd"] == 2


def test_vix_opposed_in_lots():
    whale = {9 * 60 + 30 + i: 10.0 for i in range(5)}
    crowd = {9 * 60 + 30 + i: -4.0 for i in range(5)}
    out = wf.vix_opposed(whale, crowd, 9 * 60 + 34)
    assert out == [{"t0": "09:30", "t1": "09:35", "whale": 50, "crowd": -20, "whales_side": "up"}]
    assert wf.vix_opposed({570: 1.0}, {570: -1.0}, 574) == []          # under the 10-lot floor


def test_vix_tape_absorption_and_heavy():
    # sellers 5 minutes in a row, customers pushed SHORT VIX (sd < 0) but VIX rose -> absorption
    rows = [vrow(9, 30 + i, "crowd", 10, 40, sd=-500, spot=20 + 0.05 * i) for i in range(6)]
    rows.append(vrow(9, 36, "whale", 0, 300, sd=-900, spot=20.4))
    norm = {9 * 60 + 35: 20.0}
    out = wf.vix_build(rows, D, 9 * 60 + 36, 20, norm)
    t = out["tape"]
    assert t["who"] == "SELLERS" and t["strength"] == "strong" and t["push"] == "short VIX"
    assert t["vix_move"] > 0 and "absorbing" in t["absorption"]
    assert t["heavy"] == {"t": "09:35", "n": 50, "x": 2.5, "basis": "median of 09:35 over earlier sessions"}
    assert t["whale_pct"] == round(100 * 300 / (40 + 160 + 300))


def test_vix_rows_and_norm_from_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from trading_intel.memory.models import TasCrowdMinute as T
    eng = create_engine("sqlite://")
    T.__table__.create(eng)
    sf = sessionmaker(eng)
    with sf() as s:
        for k, day in enumerate([date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), D]):
            for root, b in (("VIX", 10 + k), ("VIXW", 1), ("SPX", 999)):
                s.add(T(trade_date=day, minute=datetime(day.year, day.month, day.day, 9, 31), root=root, expiry=day,
                        strike=20.0, cp="C", bucket="crowd", n_prints=1, buy_contracts=b, sell_contracts=2,
                        other_contracts=0, buy_premium=0, sell_premium=0, signed_delta=5, spot=19.5,
                        last_ts=datetime(day.year, day.month, day.day, 9, 31, 30)))
        s.commit()
    rows = wf.load_vix_rows(sf, D)
    assert len(rows) == 1 and rows[0]["buy"] == 13 + 1 and rows[0]["sell"] == 4        # VIX + VIXW, no SPX
    norm = wf.load_vix_norm(sf, D)
    assert norm == {9 * 60 + 31: 16.0}             # totals 15, 16, 17 (VIX + VIXW bought + sold) -> median 16
