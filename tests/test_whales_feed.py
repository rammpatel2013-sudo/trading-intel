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
