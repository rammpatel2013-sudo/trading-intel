"""Call-/put-wall conviction — Mithil's 3-condition "does this level HOLD?" read.

A wall (largest <=60 DTE gamma-OI strike above/below spot) is only a tradeable
ceiling/floor when ALL of these line up:

1. **Stable** — the wall strike has stopped ratcheting (unchanged over the last
   ``stable_sessions`` EOD snapshots).
2. **At the level** — spot is within ``at_pct`` of the strike (a wall you have not
   reached says nothing today).
3. **Fixed-strike vol at the wall is offered / calm, not bid** — calm or falling IV
   at that exact strike means dealers are comfortable short gamma there and defend
   it (HOLD / pin); IV being bid means they are paying up rather than defending
   (BREAK through).

Into earnings, fixed-strike vol is structurally bid at every strike, so condition 3
is unreadable ⇒ WAIT. A wall strike too thin to trust (low OI rank / volume) is
flagged, not hidden.

Vol behaviour says whether a level HOLDS vs BREAKS — never which way price goes.
Descriptor only (FlashAlpha rule 4); pure, no I/O.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

STABLE_SESSIONS = 3
AT_PCT = 0.01  # within 1% = "at the level"
NEAR_PCT = 0.03  # within 3% = approaching
FSV_LOOKBACK = 3  # sessions over which fixed-strike vol drift is measured
FSV_BID_PTS = 0.5  # vol points (IV in %) — >= this rise = bid
EARNINGS_WINDOW_DAYS = 10
THIN_OI_RANK = 0.5  # wall strike's OI below the median strike ⇒ thin
THIN_MIN_VOLUME = 100


@dataclass
class WallRead:
    side: str  # "call" | "put"
    strike: float | None
    verdict: str  # HOLD | BREAK | NO READ | WAIT
    stable: bool | None = None
    proximity: str | None = None  # at | approaching | far | through
    dist_pct: float | None = None
    fsv_state: str | None = None  # offered | calm | bid | n/a
    fsv_change_pts: float | None = None
    thin: bool | None = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def wall_stable(history: Sequence[float | None], *, sessions: int = STABLE_SESSIONS) -> bool | None:
    """True if the last ``sessions`` wall strikes are identical (None if too short)."""
    vals = [h for h in history if h is not None]
    if len(vals) < sessions:
        return None
    tail = vals[-sessions:]
    return all(abs(x - tail[-1]) < 1e-9 for x in tail)


def proximity(side: str, spot: float, strike: float) -> tuple[str, float]:
    """Where spot sits vs the wall. ``dist`` is signed (+ = wall still ahead)."""
    dist = (strike - spot) / spot if side == "call" else (spot - strike) / spot
    if dist < -AT_PCT:
        return "through", dist
    if abs(dist) <= AT_PCT:
        return "at", dist
    if dist <= NEAR_PCT:
        return "approaching", dist
    return "far", dist


def fsv_state(series: Sequence[float | None], *, lookback: int = FSV_LOOKBACK) -> tuple[str, float | None]:
    """Fixed-strike vol drift at the wall over ``lookback`` sessions.

    ``series`` = IV at the fixed strike (decimal, e.g. 0.42), oldest first.
    Returns (offered|calm|bid|n/a, change in vol points).
    """
    vals = [v for v in series if v is not None and v > 0]
    if len(vals) < 2:
        return "n/a", None
    base = vals[-(lookback + 1)] if len(vals) > lookback else vals[0]
    chg = (vals[-1] - base) * 100.0
    if chg >= FSV_BID_PTS:
        return "bid", chg
    if chg <= -FSV_BID_PTS:
        return "offered", chg
    return "calm", chg


def is_thin(oi_rank: float | None, volume: float | None) -> bool | None:
    """Wall strike too illiquid to trust its vol print."""
    if oi_rank is None and volume is None:
        return None
    if oi_rank is not None and oi_rank < THIN_OI_RANK:
        return True
    return volume is not None and volume < THIN_MIN_VOLUME


def oi_rank(strike_oi: float | None, chain_oi: Sequence[float | None]) -> float | None:
    """Percentile rank (0-1) of the wall strike's OI among today's strikes."""
    pool = [x for x in chain_oi if x is not None]
    if strike_oi is None or not pool:
        return None
    return sum(1 for x in pool if x <= strike_oi) / len(pool)


def earnings_days(today: date, upcoming: Sequence[date]) -> int | None:
    """Days to the next earnings date on/after ``today`` (None if none known)."""
    fut = sorted(d for d in upcoming if d >= today)
    return (fut[0] - today).days if fut else None


def read_wall(
    side: str,
    *,
    spot: float | None,
    wall_history: Sequence[float | None],
    fsv_series: Sequence[float | None],
    earnings_in_days: int | None = None,
    oi_rank_value: float | None = None,
    wall_volume: float | None = None,
) -> WallRead:
    """Score one wall. ``wall_history`` oldest first; last element = today's wall."""
    strike = next((w for w in reversed(wall_history) if w is not None), None)
    r = WallRead(side=side, strike=strike, verdict="NO READ")
    if strike is None or not spot:
        r.reasons.append("no wall / spot")
        return r

    r.stable = wall_stable(wall_history)
    r.proximity, r.dist_pct = proximity(side, float(spot), float(strike))
    r.fsv_state, r.fsv_change_pts = fsv_state(fsv_series)
    r.thin = is_thin(oi_rank_value, wall_volume)

    if earnings_in_days is not None and 0 <= earnings_in_days <= EARNINGS_WINDOW_DAYS:
        r.verdict = "WAIT"
        r.reasons.append(f"earnings in {earnings_in_days}d — fixed-strike vol event-bid, unreadable")
        return r
    if r.stable is False:
        r.reasons.append("wall still migrating")
    elif r.stable is None:
        r.reasons.append("wall history too short")
    if r.proximity == "through":
        r.reasons.append("spot already through the wall")
    elif r.proximity != "at":
        r.reasons.append(f"spot {abs(r.dist_pct) * 100:.1f}% from the wall")
    if r.fsv_state == "n/a":
        r.reasons.append("no fixed-strike vol history")
    if r.reasons:
        return r

    r.verdict = "BREAK" if r.fsv_state == "bid" else "HOLD"
    r.reasons.append(
        f"fixed-strike vol {r.fsv_state} ({r.fsv_change_pts:+.1f} pts/{FSV_LOOKBACK}d)"
    )
    if r.thin:
        r.reasons.append("wall strike thin — low confidence")
    return r
