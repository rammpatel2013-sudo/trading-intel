"""Tests for scripts/vs_morning_card.py (2026-11-13, VS plan step 7) - pure logic, no DB."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import vs_morning_card as mc  # noqa: E402


def _p(ts, k, cp, size, price, grp=None, root="SPXW"):
    return {"ts": ts, "expiry": "2026-11-16", "strike": k, "cp": cp, "size": size, "price": price,
            "leg_group": grp, "root": root}


def test_legs_of_one_order_are_read_together():
    prints = [_p("10:00:00.123", 7690.0, "C", 1000, 0.40, "g1"), _p("10:00:00.123", 7790.0, "C", 1000, 0.15, "g1"),
              _p("10:00:01.500", 7500.0, "P", 200, 12.0), _p("10:00:02.000", 7450.0, "P", 50, 3.0)]
    sides = [+1, -1, -1, +1]
    o = mc.group_orders(prints, sides)
    assert o[0]["name"] == "7,500 put" and o[0]["premium"] == 200 * 12.0 * 100    # biggest premium first
    spread = next(x for x in o if "spread" in x["name"])
    assert spread["name"] == "7,690/7,790 call spread" and spread["size"] == 1000
    assert mc.order_side(spread) == "customer bought"


def test_same_millisecond_without_a_group_is_one_order():
    prints = [_p("10:05:00.001", 7500.0, "P", 300, 5.0), _p("10:05:00.001", 7400.0, "P", 300, 2.0)]
    o = mc.group_orders(prints, [-1, +1])
    assert len(o) == 1 and o[0]["name"] == "7,400/7,500 put spread" and mc.order_side(o[0]) == "customer bought"


def test_balance_and_tests():
    lv = mc.balance_tests({7500.0: -3.0, 7525.0: 1.0, 7550.0: 5.0, 7575.0: -1.0, 7600.0: -4.0, 7900.0: -9.0}, 7560.0, 60.0)
    assert lv == {"balance": 7550.0, "up_test": 7600.0, "down_test": 7500.0, "band": 60.0}


def test_caption_and_card_render():
    P = {"spot": 7592.0, "session": "2026-11-16", "written": "2026-11-16 08:45", "ref": "2026-11-16 09:30",
         "book": {"ts": "2026-11-16 08:35", "oi_note": "open interest as of the prior close"},
         "grid": {"lo": 7400.0, "step": 1.0, "n": 401}, "full": [0.01 * (i - 150) for i in range(401)],
         "ex0": [0.005 * (i - 160) for i in range(401)], "at_spot": {"full": 0.42, "ex0": 0.16},
         "pct": {"full": 83, "ex0": 41}, "flips": {"full": {"nearest": 7550.0}, "ex0": {"nearest": 7560.0}},
         "hedge": [{"move_pct": -1.0, "spx": 7516.1, "es": 9000, "es_ex0": 2000},
                   {"move_pct": 1.0, "spx": 7667.9, "es": -12000, "es_ex0": -3000}],
         "charm": {"total": [0.1] * 401, "red_flag": {"flag": True}, "straddle": 40.0}, "signed": {"ok": False}, "odds": {}}
    cap = mc.caption(P)
    assert cap.startswith("SPX 7,592: dealers LONG gamma (83 pct) - moves get absorbed; flip 7,550.")
    assert "RED FLAG" in cap.splitlines()[1] and "-1%: dealers buy" in cap
    h = mc.card_html(P, [], {"balance": 7600.0, "up_test": None, "down_test": 7500.0, "band": 80.0}, "naive book")
    assert "SPX morning card · 2026-11-16" in h and "Balance <b>7,600</b>" in h and "no SPX tape" in h and "<svg" in h
