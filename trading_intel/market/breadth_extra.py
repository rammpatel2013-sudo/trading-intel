"""Extra breadth counts from the constituent bars the breadth job already pulls. Pure.

2026-11-13 overnight build (brief item 5: Stockbee breadth, tape pillar, bottom checklist,
setup supply - Mithil's 11/13 backlog cut). S&P 500 universe (the job's constituents), not
Stockbee's all-US universe: trust the shape, not the absolute level.

- up_volume / down_volume: share volume of today's advancers / decliners (90% day test).
- up4 / down4: names up / down >= 4% today (Stockbee Market Monitor primary).
- up25q / down25q: names up / down >= 25% over the last 65 sessions (quarter).
- pct_above_40: % above their 40-day average (Stockbee's T2108 idea).
- setups_base: tight base near highs = within 5% of the 52-week closing high and the last
  10 closes inside a 6% range. setups_pullback: pullback to a rising 50-day = close within
  3% above the 50-day, 50-day rising over 10 sessions, close above the 200-day.
Descriptor only (rule 4).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def _sma(v: Sequence[float], n: int) -> float | None:
    return sum(v[-n:]) / n if len(v) >= n else None


def up_down_volume(closes: Mapping[str, Sequence[float]],
                   last_volumes: Mapping[str, float]) -> tuple[int | None, int | None]:
    if not last_volumes:
        return None, None
    up = down = 0
    for sym, c in closes.items():
        vol = last_volumes.get(sym)
        if vol is None or len(c) < 2:
            continue
        if c[-1] > c[-2]:
            up += int(vol)
        elif c[-1] < c[-2]:
            down += int(vol)
    return up, down


def _is_base(c: Sequence[float]) -> bool:
    if len(c) < 60:
        return False
    hi = max(c[-252:])
    last10 = c[-10:]
    return c[-1] >= 0.95 * hi and (max(last10) - min(last10)) <= 0.06 * c[-1]


def _is_pullback(c: Sequence[float]) -> bool:
    if len(c) < 210:
        return False
    m50, m50_10 = _sma(c, 50), _sma(c[:-10], 50)
    m200 = _sma(c, 200)
    return bool(m50 and m50_10 and m200 and m50 > m50_10 and c[-1] > m200
                and m50 <= c[-1] <= 1.03 * m50)


def extras(closes: Mapping[str, Sequence[float]],
           last_volumes: Mapping[str, float] | None = None) -> dict:
    """All extra counts for the newest session (keys = breadth_snapshots columns)."""
    up4 = down4 = up25 = down25 = above40 = n40 = base = pull = 0
    for c in closes.values():
        if len(c) >= 2 and c[-2]:
            ch = c[-1] / c[-2] - 1
            up4 += ch >= 0.04
            down4 += ch <= -0.04
        if len(c) >= 66 and c[-66]:
            q = c[-1] / c[-66] - 1
            up25 += q >= 0.25
            down25 += q <= -0.25
        m40 = _sma(c, 40)
        if m40:
            n40 += 1
            above40 += c[-1] > m40
        base += _is_base(c)
        pull += _is_pullback(c)
    upv, downv = up_down_volume(closes, last_volumes or {})
    return {
        "up_volume": upv, "down_volume": downv,
        "up4": up4, "down4": down4, "up25q": up25, "down25q": down25,
        "pct_above_40": round(100 * above40 / n40) if n40 else None,
        "setups_base": base, "setups_pullback": pull,
    }
