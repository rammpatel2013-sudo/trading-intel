"""The market gate: "is it safe to buy stocks right now?" - green / amber / red (E2). Pure.

Rule v1 (set 2026-11-14, NOT yet tested - thresholds are starting points). Each input casts +1
(healthy), 0, or -1 (unhealthy); missing inputs do not vote:

1. damage     median stock within 10% of its high: +1 · more than 20% below: -1
2. repair     20-day repair flow above +5: +1 · below -5: -1
3. nh_nl      NYSE+Nasdaq new highs - new lows, 10-day average % of issues: > +1: +1 · < -1: -1
4. above_50   % of stocks above their 50-day line: > 60: +1 · < 40: -1
5. above_200  % above the 200-day line: > 60: +1 · < 40: -1
6. index      SPY above its 50- and 200-day lines: +1 · below the 200-day: -1
7. stockbee   stocks up 25% in a quarter vs down 25% (Stockbee's tie-breaker ratio): > 1.5: +1 · < 0.67: -1
8. beta_reset label RESET (low beta broke, high beta held = washout behind us): +1 ·
              "low beta breaking, high beta not holding": -1

Score >= +3 green · <= -2 red · otherwise amber. ``trend`` compares with the score 5 sessions ago.
Descriptor only (rule 4) - it says what the tape looks like, it does not place trades.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

GREEN_AT = 3
RED_AT = -2


def _vote(x: float | None, up: float, down: float) -> int | None:
    if x is None or x != x:
        return None
    return 1 if x > up else (-1 if x < down else 0)


def votes(inp: Mapping[str, Any]) -> dict[str, int]:
    v: dict[str, int | None] = {
        "damage": _vote(inp.get("median_drawdown"), -10.0, -20.0),
        "repair": _vote(inp.get("repair_flow"), 5.0, -5.0),
        "nh_nl": _vote(inp.get("nhnl_10d"), 1.0, -1.0),
        "above_50": _vote(inp.get("pct_above_50"), 60.0, 40.0),
        "above_200": _vote(inp.get("pct_above_200"), 60.0, 40.0),
        "stockbee": _vote(inp.get("q25_ratio"), 1.5, 0.67),
    }
    a50, a200 = inp.get("spy_above_50"), inp.get("spy_above_200")
    if a200 is not None:
        v["index"] = 1 if (a200 and a50) else (-1 if not a200 else 0)
    label = inp.get("beta_reset_label")
    if label:
        v["beta_reset"] = (
            1
            if label == "RESET"
            else (-1 if label.startswith("low beta breaking, high beta not") else 0)
        )
    return {k: x for k, x in v.items() if x is not None}


def color(score: int) -> str:
    return "green" if score >= GREEN_AT else ("red" if score <= RED_AT else "amber")


def decide(inp: Mapping[str, Any], *, score_5d_ago: int | None = None) -> dict[str, Any]:
    v = votes(inp)
    score = sum(v.values())
    trend = None
    if score_5d_ago is not None:
        trend = (
            "improving"
            if score > score_5d_ago
            else ("worsening" if score < score_5d_ago else "flat")
        )
    return {"color": color(score), "score": score, "votes": v, "trend": trend, "n_votes": len(v)}


PLAIN = {
    "damage": "how far the typical stock is below its 1-year high",
    "repair": "are stocks moving back toward their highs (+) or further away (-) over 20 days",
    "nh_nl": "NYSE+Nasdaq new highs minus new lows (10-day average)",
    "above_50": "share of stocks above their 50-day average",
    "above_200": "share of stocks above their 200-day average",
    "index": "SPY vs its 50- and 200-day averages",
    "stockbee": "stocks up 25% in a quarter vs stocks down 25%",
    "beta_reset": "your beta-reset rule (low beta broke and high beta held = washout done)",
}


def headline(g: Mapping[str, Any]) -> str:
    c = str(g.get("color", "?")).upper()
    meaning = {
        "GREEN": "conditions support buying stocks",
        "AMBER": "mixed - buy only the strongest setups, smaller size",
        "RED": "damage is spreading - watch, don't buy",
    }.get(c, "")
    t = g.get("trend")
    return f"{c} ({g.get('score', 0):+d} of {g.get('n_votes', 0)} votes) - {meaning}" + (
        f"; {t}" if t else ""
    )
