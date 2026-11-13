"""am_feed Doc reader - old and 2026-11-13 report formats (synthetic text, made-up numbers)."""
import importlib.util
from pathlib import Path

_P = Path(__file__).resolve().parents[2] / "scripts" / "cvxmap" / "am_feed.py"
_S = importlib.util.spec_from_file_location("am_feed_under_test", _P)
am = importlib.util.module_from_spec(_S)
_S.loader.exec_module(am)

OLD = """SPX 0DTE MARKET REPORT: MON 11/02
ROAD MAP
THE PIVOT: 5,100
IF SPX converts 5,120 on a 5-minute close, THEN 5,140–5,145.
IF 5,080 is lost, THEN 5,060.
TRADINGVIEW STRINGS
0DTE MAIN
$SPX: Pivot, 5100, Call wall, 5150, Put wall, 5050
"""

TEASER = "The Cheat Card, the SPX Road Map, premium strikes, and TradingView strings."

NEW = f"""Intro line about the day near 5,100.
THE READ
🟢 Above 5,120–5,125: The upside chute opens toward 5,140–5,145, then the 5,160 brake.
🟡 5,090–5,100: Equilibrium. Expect friction.
🔴 Below 5,085–5,075: Acceleration risk toward 5,050.
• Expected range: 5,060 / 5,155
LEVELS & LINES
• 1.5× straddle: 5,040 / 5,180
ABOVE THE PIVOT
5,160 — SECONDARY CEILING
5,120–5,125 — 20-DAY / UPSIDE GATE
THE PIVOT
5,100 — EQUILIBRIUM
UNDERNEATH
5,075 — OVERNIGHT LOW / TRANSITION
5,045–5,055 — THURSDAY'S LOW / CATCH
HOW TO USE THE MAP
BELOW THE LINE TODAY
{TEASER}
CHEAT CARD
THE ONE THING
5,100 is the pivot.
⬆️ UP PATH
5,125 → 5,140–5,145 → 5,160
⬇️ DOWN PATH
5,085 → 5,075 → 5,050
"""


def test_old_format_unchanged():
    d = am.parse_doc(OLD)
    assert [x["v"] for x in d["levels"]["SPX"]] == [5100, 5150, 5050]
    assert d["pivot"] == 5100
    assert len(d["scenarios"]) == 2


def test_new_format_ladder_paths_and_read_lines():
    d = am.parse_doc(NEW, "SPX 0DTE MARKET REPORT: FRI 11/13")
    lv = d["levels"]["SPX"]
    assert [x["v"] for x in lv] == [5160, 5122, 5100, 5075, 5050]
    assert [x["role"] for x in lv] == ["pos", "trig", "piv", "trig", "pos"]
    assert lv[1]["lo"] == 5120 and lv[1]["v2"] == 5125
    assert d["pivot"] == 5100 and d["chop"] == [5090, 5100] and d["em"] == [5060, 5155]
    assert {r["name"] for r in d["rails"]} == {"STR", "STR 1.5"}
    assert d["one_thing"] == "5,100 is the pivot."
    assert d["up_path"][0] == [5125, 5125] and d["down_path"][-1] == [5050, 5050]
    up, dn = d["scenarios"]
    assert (up["dir"], up["level"], up["targets"]) == ("up", 5125, [[5140, 5145], [5160, 5160]])
    assert (dn["dir"], dn["level"], dn["targets"]) == ("down", 5075, [[5050, 5050]])


def test_teaser_line_does_not_hide_the_real_tradingview_section():
    d = am.parse_doc(NEW + "TRADINGVIEW STRINGS\n0DTE MAIN\n$SPX: Pivot, 5100, Gate, 5125\n")
    assert [x["v"] for x in d["levels"]["SPX"]] == [5100, 5125]
