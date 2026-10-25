"""Scoring the morning MAP card against the tape (build plan §7). Pure, no I/O.

Grades one session from the SPX daily high / low / close:

* **bucket** — which of the three ways it actually closed (below the floor,
  between, above the cap), to be compared with the odds the card gave.
* **touched / held / broke** — a wall that is tagged *touched* was traded
  through intraday; *held* means price came back and closed inside it. The
  distinction matters: a floor that is touched every day and holds every day
  is working, and a "never touched" record would hide that.
* **inside the 70% range** — the calibration line. Over enough sessions the
  70% range should contain roughly 70% of closes; materially more means the
  range is too wide, less means too narrow.

Nothing here scores a probability. The three-ways odds left the brief until
Breeden-Litzenberger can be computed from banked mid prices, and grading
against the flat-vol approximation we removed would have been worse than not
grading at all.

Everything is ``None`` when the input it needs is missing — an ungraded field
must never be confused with a failed grade, because the scorecard counts them.
"""

from __future__ import annotations

from typing import Any


def grade_session(
    *,
    floor: float | None,
    cap: float | None,
    range70_lo: float | None,
    range70_hi: float | None,
    high: float | None,
    low: float | None,
    close: float | None,
) -> dict[str, Any]:
    """Score one session. All outputs are ``None`` where the inputs don't allow a call."""
    out: dict[str, Any] = {
        "bucket": None,
        "floor_touched": None,
        "cap_touched": None,
        "floor_held": None,
        "cap_held": None,
        "inside_range70": None,
        "wall_broke": None,
    }
    if close is None:
        return out

    if floor is not None and cap is not None:
        if close < floor:
            out["bucket"] = "below"
        elif close > cap:
            out["bucket"] = "above"
        else:
            out["bucket"] = "between"
        out["wall_broke"] = close < floor or close > cap

    if floor is not None and low is not None:
        out["floor_touched"] = low <= floor
        # "Held" is only meaningful once it was actually tested.
        out["floor_held"] = (close > floor) if low <= floor else None

    if cap is not None and high is not None:
        out["cap_touched"] = high >= cap
        out["cap_held"] = (close < cap) if high >= cap else None

    if range70_lo is not None and range70_hi is not None:
        lo, hi = min(range70_lo, range70_hi), max(range70_lo, range70_hi)
        out["inside_range70"] = lo <= close <= hi

    return out


def scorecard(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll graded sessions into the Monday card.

    ``rows`` are graded ``map_daily`` records as dicts. Returns the hit counts,
    the 70%-range calibration, and the soft-vs-firm break rates that decide
    whether the fragility flag survives its two-month review — the whole point
    of recording it is to be able to drop it if soft days don't break more.
    """
    graded = [r for r in rows if r.get("bucket")]
    n = len(graded)
    card: dict[str, Any] = {
        "n": n,
        "buckets": {"below": 0, "between": 0, "above": 0},
        "range70_hits": None,
        "range70_rate": None,
        "range70_expected": 0.70,
        "floor_tests": 0,
        "floor_holds": 0,
        "cap_tests": 0,
        "cap_holds": 0,
        "soft": {"n": 0, "breaks": 0, "rate": None},
        "firm": {"n": 0, "breaks": 0, "rate": None},
        "fragility_verdict": None,
    }
    if not n:
        return card

    for r in graded:
        b = r.get("bucket")
        if b in card["buckets"]:
            card["buckets"][b] += 1
        if r.get("floor_touched"):
            card["floor_tests"] += 1
            if r.get("floor_held"):
                card["floor_holds"] += 1
        if r.get("cap_touched"):
            card["cap_tests"] += 1
            if r.get("cap_held"):
                card["cap_holds"] += 1

    in70 = [r for r in graded if r.get("inside_range70") is not None]
    if in70:
        hits = sum(1 for r in in70 if r["inside_range70"])
        card["range70_hits"] = hits
        card["range70_n"] = len(in70)
        card["range70_rate"] = hits / len(in70)

    for r in graded:
        firm = r.get("levels_firm")
        if firm is None or r.get("wall_broke") is None:
            continue
        key = "firm" if firm else "soft"
        card[key]["n"] += 1
        if r["wall_broke"]:
            card[key]["breaks"] += 1
    for key in ("soft", "firm"):
        k = card[key]
        k["rate"] = (k["breaks"] / k["n"]) if k["n"] else None

    s, f = card["soft"]["rate"], card["firm"]["rate"]
    if s is not None and f is not None and card["soft"]["n"] >= 5 and card["firm"]["n"] >= 5:
        # The flag earns its place only if soft days break MORE often.
        card["fragility_verdict"] = (
            "working — soft days break more often"
            if s > f
            else "not earning its place — soft days do not break more often"
        )
    return card
