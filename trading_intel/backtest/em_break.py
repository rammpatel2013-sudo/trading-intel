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


@dataclass(frozen=True, slots=True)
class ScaleLeg:
    """One profit-taking leg: close ``fraction`` of the position at price ``target``."""

    target: float
    fraction: float  # 0 < fraction <= 1


@dataclass(frozen=True, slots=True)
class ScaledOutcome:
    """Result of managing a structure with partial (scaled) exits."""

    entry: float
    stop: float
    blended_r: float  # position-weighted R across every partial exit
    legs_hit: int
    closed_fraction: float  # fraction booked at targets (0..1)
    result: str  # "target" | "stop" | "open" | "mixed"
    exit_date: date | None
    days_held: int


def legs_from_r_multiples(
    entry: float, stop: float, r_multiples: Sequence[float], fractions: Sequence[float]
) -> list[ScaleLeg]:
    """Build scale legs at ``entry + r * (entry - stop)`` for each R multiple.

    ``fractions[i]`` is the portion of the position closed at ``r_multiples[i]`` R.
    """
    risk = entry - stop
    if risk <= 0:
        raise ValueError("need entry > stop")
    if len(r_multiples) != len(fractions):
        raise ValueError("r_multiples and fractions must be the same length")
    return [
        ScaleLeg(entry + float(r) * risk, float(f))
        for r, f in zip(r_multiples, fractions, strict=True)
    ]


def scaled_exit_r(
    entry: float,
    stop: float,
    legs: Sequence[ScaleLeg],
    path: Sequence[Bar],
    *,
    max_days: int | None = None,
    stop_to_breakeven_after_first: bool = True,
) -> ScaledOutcome | None:
    """Blended R of a partial scale-out exit (the Yamco T1/T2/T3 method).

    Walk ``path``; when a leg's target trades, book that ``fraction`` at the leg's R and
    -- after the first target, if enabled -- ratchet the stop to breakeven so a winner
    can't turn red ("green doesn't become red"). A stop touch closes the remaining
    position at the (possibly breakeven) stop; anything still open at the horizon is
    marked to the last close. The unclosed remainder (fractions summing to < 1) rides to
    stop/horizon.

    Geometry: need ``stop < entry`` and every target > entry, fractions in (0, 1] summing
    to <= 1. A same-bar stop+target resolves conservatively to the stop (matching
    ``evaluate_outcome``). Returns ``None`` on invalid geometry or an empty path. See
    ``docs/playbooks/risk_management.md``.
    """
    if not path or stop >= entry:
        return None
    ordered = sorted(legs, key=lambda leg: leg.target)
    total_frac = sum(leg.fraction for leg in ordered)
    if not ordered or ordered[0].target <= entry:
        return None
    if any(leg.fraction <= 0 for leg in ordered) or total_frac > 1.0 + 1e-9:
        return None

    risk = entry - stop
    horizon = len(path) if max_days is None else min(len(path), max(1, int(max_days)))
    remaining = 1.0
    realized_r = 0.0
    legs_hit = 0
    idx = 0
    cur_stop = stop
    exit_i = horizon - 1
    result = "open"
    resolved = False

    for i in range(horizon):
        bar = path[i]
        if bar.low <= cur_stop:  # conservative: stop wins an ambiguous same-bar touch
            realized_r += remaining * (cur_stop - entry) / risk
            remaining = 0.0
            exit_i = i
            result = "stop" if legs_hit == 0 else "mixed"
            resolved = True
            break
        while idx < len(ordered) and bar.high >= ordered[idx].target:
            leg = ordered[idx]
            realized_r += leg.fraction * (leg.target - entry) / risk
            remaining -= leg.fraction
            legs_hit += 1
            idx += 1
            if legs_hit == 1 and stop_to_breakeven_after_first:
                cur_stop = entry
        if remaining <= 1e-9:
            remaining = 0.0
            exit_i = i
            result = "target"
            resolved = True
            break

    if not resolved:
        last = path[horizon - 1]
        realized_r += remaining * (last.close - entry) / risk
        exit_i = horizon - 1
        result = "mixed" if legs_hit else "open"

    closed = sum(leg.fraction for leg in ordered[:legs_hit])
    return ScaledOutcome(
        entry=entry,
        stop=stop,
        blended_r=realized_r,
        legs_hit=legs_hit,
        closed_fraction=min(1.0, closed),
        result=result,
        exit_date=path[exit_i].d,
        days_held=exit_i + 1,
    )
