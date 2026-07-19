"""Pure outcome evaluation for the EM-break re-entry pattern (P6 backtest core).

Given a defined-risk UPSIDE re-entry — enter near the put wall after stabilization,
target the call wall, stop on a put-wall break — walk a forward OHLC path and decide
whether the target or the stop was hit first, with the realized R-multiple. Then
``summarize`` / ``summarize_by_bucket`` roll a set of outcomes into hit-rate /
expectancy stats, optionally split by conviction so we can check the gate actually
discriminates (higher conviction -> better outcomes).

Pure transforms, no I/O (the DB/CVForge plumbing lives in ``cases.py`` /
``reconstruct.py``), so the decision logic is unit-tested deterministically. This is
the validation engine that lets us eventually drop ``experimental=True`` on
``EM_BREAK_REENTRY`` — see ``docs/em_break_backtest.md`` for the success criteria.

Convention: ``stop < entry < target`` (a long/upside structure). R-multiple is
measured in units of initial risk ``entry - stop``: +reward/risk on a win, -1 on a
stop, and the marked-to-last fraction on a still-open trade.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import fmean, median


@dataclass(frozen=True, slots=True)
class Bar:
    """One forward session used to walk the trade."""

    d: date
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Outcome:
    """Resolved (or still-open) result of one re-entry structure."""

    entry: float
    target: float
    stop: float
    result: str  # "win" | "loss" | "open"
    exit_date: date | None
    exit_price: float
    days_held: int
    r_multiple: float
    mfe: float  # max favourable excursion (price above entry), >= 0
    mae: float  # max adverse excursion (price below entry), <= 0


def bars_from_rows(rows: Sequence[tuple[date, float, float, float]]) -> list[Bar]:
    """Build ``Bar``s from ``(date, high, low, close)`` tuples (oldest first)."""
    return [Bar(d, float(h), float(low), float(c)) for d, h, low, c in rows]


def evaluate_outcome(
    entry: float,
    target: float,
    stop: float,
    path: Sequence[Bar],
    *,
    max_days: int | None = None,
) -> Outcome | None:
    """Walk ``path`` and classify the trade.

    Returns ``None`` when the geometry is invalid (need ``stop < entry < target``)
    or the path is empty. A bar that touches BOTH target and stop is resolved
    conservatively as a stop (we can't see intrabar order). Without a hit by
    ``max_days`` (or the end of the path) the trade is ``open``, marked to the last
    close.
    """
    if not path:
        return None
    if not (stop < entry < target):
        return None

    risk = entry - stop
    horizon = len(path) if max_days is None else min(len(path), max(1, int(max_days)))
    mfe = 0.0
    mae = 0.0
    for i in range(horizon):
        bar = path[i]
        mfe = max(mfe, bar.high - entry)
        mae = min(mae, bar.low - entry)
        hit_stop = bar.low <= stop
        hit_target = bar.high >= target
        if hit_stop:  # conservative: stop wins an ambiguous same-bar touch
            return Outcome(entry, target, stop, "loss", bar.d, stop, i + 1, -1.0, mfe, mae)
        if hit_target:
            return Outcome(
                entry,
                target,
                stop,
                "win",
                bar.d,
                target,
                i + 1,
                (target - entry) / risk,
                mfe,
                mae,
            )

    last = path[horizon - 1]
    return Outcome(
        entry,
        target,
        stop,
        "open",
        last.d,
        last.close,
        horizon,
        (last.close - entry) / risk,
        mfe,
        mae,
    )


def summarize(outcomes: Sequence[Outcome]) -> dict:
    """Hit-rate / expectancy over a set of outcomes.

    Hit-rate and average R are computed over CLOSED trades only (win/loss); open
    trades are counted separately so an unfinished sample doesn't flatter the stats.
    """
    n = len(outcomes)
    closed = [o for o in outcomes if o.result in ("win", "loss")]
    wins = [o for o in closed if o.result == "win"]
    n_closed = len(closed)
    return {
        "n": n,
        "n_closed": n_closed,
        "n_open": n - n_closed,
        "wins": len(wins),
        "losses": n_closed - len(wins),
        "hit_rate": (len(wins) / n_closed) if n_closed else None,
        "avg_r": fmean([o.r_multiple for o in closed]) if closed else None,
        "expectancy_r": fmean([o.r_multiple for o in closed]) if closed else None,
        "median_days_to_exit": median([o.days_held for o in closed]) if closed else None,
        "avg_mfe": fmean([o.mfe for o in outcomes]) if outcomes else None,
        "avg_mae": fmean([o.mae for o in outcomes]) if outcomes else None,
    }


def summarize_by_bucket(
    pairs: Sequence[tuple[float, Outcome]],
    *,
    edges: Sequence[float] = (70.0, 85.0),
) -> dict:
    """Summarize outcomes split into conviction buckets.

    ``pairs`` is ``(conviction, Outcome)``. ``edges`` are the internal cut points;
    with the default ``(70, 85)`` the buckets are ``[..,70) [70,85) [85,..]``. The
    validation question — does higher conviction realize a higher hit-rate / R? —
    reads straight off this.
    """
    cuts = sorted(edges)
    labels: list[str] = []
    lo = "-inf"
    for c in cuts:
        labels.append(f"[{lo},{c:g})")
        lo = f"{c:g}"
    labels.append(f"[{lo},inf]")

    groups: dict[str, list[Outcome]] = {lab: [] for lab in labels}
    for conv, oc in pairs:
        idx = 0
        while idx < len(cuts) and conv >= cuts[idx]:
            idx += 1
        groups[labels[idx]].append(oc)

    return {lab: summarize(ocs) for lab, ocs in groups.items()}
