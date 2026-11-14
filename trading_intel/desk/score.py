"""Desk brief - score picks after the fact (2026-11-14). Pure; prices passed in.

Rules fixed before any result was seen (project doc ``claude/desk-brief-paper-track.md``):
entry = the OPEN of the first session after the brief date (he publishes the next day), exit =
the close after N sessions (entry day = session 1), no stops, equal weight. Each basket is compared
with SPY over the same window and with the rest of the same day's screen.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

HORIZONS = (5, 10, 20)
ROLES = ("top", "two_leg", "rest", "div_a", "div_b")


def trade(bars: Sequence[tuple], after: date, n: int) -> float | None:
    """% return: open of the first bar after ``after`` -> close of the n-th bar. None if not there yet."""
    fut = [b for b in bars if b[0] > after]
    if len(fut) < n or not fut[0][1] or not fut[n - 1][4]:
        return None
    return 100.0 * (fut[n - 1][4] / fut[0][1] - 1.0)


def live(bars: Sequence[tuple], after: date) -> tuple[float | None, int]:
    """% from entry open to the latest close, and sessions held so far."""
    fut = [b for b in bars if b[0] > after]
    if not fut or not fut[0][1]:
        return None, 0
    return 100.0 * (fut[-1][4] / fut[0][1] - 1.0), len(fut)


def scorecard(
    picks: Sequence[Mapping], bars: Mapping[str, Sequence[tuple]], spy: Sequence[tuple]
) -> dict:
    """picks: [{date, source, symbol, role}] (role 'screen' = the whole screen). Returns
    {source: {role: {N: {n, mean, excess, beat}}}} plus a per-pick 'open' table for unfinished picks.
    """
    by_day: dict[tuple[str, date], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for p in picks:
        by_day[(p["source"], p["date"])][p["role"]].add(p["symbol"])
    acc: dict[str, dict[str, dict[int, list[tuple[float, float]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    opened: list[dict] = []
    for (src, d), roles in sorted(by_day.items(), key=lambda kv: kv[0][1]):
        sets = {k: set(v) for k, v in roles.items()}
        sets["rest"] = (
            sets.get("screen", set()) - sets.get("two_leg", set()) - sets.get("top", set())
        )
        for n in HORIZONS:
            bench = trade(spy, d, n)
            if bench is None:
                continue
            for role in ROLES:
                for s in sets.get(role, ()):
                    r = trade(bars.get(s, ()), d, n)
                    if r is not None:
                        acc[src][role][n].append((r, r - bench))
        spy_live, held = live(spy, d)
        if held < max(HORIZONS):
            for role in ("top", "two_leg"):
                for s in sorted(sets.get(role, ())):
                    r, k = live(bars.get(s, ()), d)
                    opened.append(
                        {
                            "date": d.isoformat(),
                            "source": src,
                            "role": role,
                            "symbol": s,
                            "ret": r,
                            "vs_spy": (
                                (r - spy_live) if r is not None and spy_live is not None else None
                            ),
                            "sessions": k,
                        }
                    )
    out: dict[str, Any] = {}
    for src, roles in acc.items():
        out[src] = {}
        for role, hs in roles.items():
            out[src][role] = {
                n: {
                    "n": len(v),
                    "mean": sum(x for x, _ in v) / len(v),
                    "excess": sum(e for _, e in v) / len(v),
                    "beat": 100.0 * sum(1 for _, e in v if e > 0) / len(v),
                }
                for n, v in sorted(hs.items())
                if v
            }
    return {"stats": out, "open": opened}
