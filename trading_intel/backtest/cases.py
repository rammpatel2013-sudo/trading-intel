"""Build EM-break re-entry backtest cases from our own banked data.

Paths (a) interim scorecard and (c) bank-forward validation both read banked
``EM_BREAK_REENTRY`` signals and walk ``quotes_daily`` forward through the pure
``backtest.em_break`` engine — no vendor call (rule 1), offline outcome scoring on
data already banked. The signal payload carries the structure (``target_call_wall``,
``stop_ref_put_wall``, ``conviction``); entry is the close on the signal date.

Read-only analytics (never writes ``signals``). See ``docs/em_break_backtest.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.backtest.em_break import (
    Bar,
    Outcome,
    evaluate_outcome,
    summarize,
    summarize_by_bucket,
)
from trading_intel.memory.models import QuoteDaily, Signal

SIGNAL_TYPE = "EM_BREAK_REENTRY"
DEFAULT_MAX_DAYS = 20
DEFAULT_STOP_FRAC = 0.03  # put-wall break buffer when the payload carries no stop ref


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Outcome (or skip reason) for one banked signal."""

    symbol: str
    entry_date: date
    conviction: float | None
    signal_id: int | None
    outcome: Outcome | None
    reason: str | None = None


def resolve_stop(
    entry: float, stop_ref: float | None, *, stop_frac: float = DEFAULT_STOP_FRAC
) -> float:
    """Put-wall stop when it sits below entry, else a fixed fractional buffer."""
    if stop_ref is not None and stop_ref < entry:
        return float(stop_ref)
    return float(entry) * (1.0 - stop_frac)


def _entry_close(session: Session, sym: str, on: date) -> tuple[date, float] | None:
    row = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == sym, QuoteDaily.date >= on)
        .order_by(QuoteDaily.date.asc())
        .limit(1)
    ).first()
    if row is None or row.close is None:
        return None
    return row.date, float(row.close)


def _forward_bars(session: Session, sym: str, after: date, *, max_days: int) -> list[Bar]:
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.high, QuoteDaily.low, QuoteDaily.close)
        .where(QuoteDaily.symbol == sym, QuoteDaily.date > after)
        .order_by(QuoteDaily.date.asc())
        .limit(max_days)
    ).all()
    return [
        Bar(r.date, float(r.high), float(r.low), float(r.close))
        for r in rows
        if r.high is not None and r.low is not None and r.close is not None
    ]


def case_from_signal(
    session: Session,
    sig: Signal,
    *,
    max_days: int = DEFAULT_MAX_DAYS,
    stop_frac: float = DEFAULT_STOP_FRAC,
) -> CaseResult:
    """Derive entry/target/stop from a signal + quotes and evaluate the outcome."""
    sym = sig.symbol
    payload = sig.payload or {}
    conviction = payload.get("conviction")
    sig_date = sig.ts.date()

    ec = _entry_close(session, sym, sig_date)
    if ec is None:
        return CaseResult(sym, sig_date, conviction, sig.id, None, "no entry quote")
    entry_date, entry = ec

    target = payload.get("target_call_wall")
    if target is None:
        return CaseResult(sym, entry_date, conviction, sig.id, None, "no target call wall")
    target = float(target)
    stop = resolve_stop(entry, payload.get("stop_ref_put_wall"), stop_frac=stop_frac)
    if not (stop < entry < target):
        return CaseResult(sym, entry_date, conviction, sig.id, None, "invalid geometry")

    path = _forward_bars(session, sym, entry_date, max_days=max_days)
    oc = evaluate_outcome(entry, target, stop, path, max_days=max_days)
    if oc is None:
        return CaseResult(sym, entry_date, conviction, sig.id, None, "no forward path yet")
    return CaseResult(sym, entry_date, conviction, sig.id, oc)


def backtest_banked(
    session: Session,
    *,
    since: date | None = None,
    max_days: int = DEFAULT_MAX_DAYS,
    stop_frac: float = DEFAULT_STOP_FRAC,
) -> dict:
    """Score every banked EM_BREAK_REENTRY signal and roll up the stats."""
    q = select(Signal).where(Signal.signal_type == SIGNAL_TYPE)
    if since is not None:
        q = q.where(Signal.ts >= datetime.combine(since, time.min))
    sigs = session.execute(q.order_by(Signal.ts.asc())).scalars().all()

    results = [case_from_signal(session, s, max_days=max_days, stop_frac=stop_frac) for s in sigs]
    scored = [r for r in results if r.outcome is not None]
    pairs = [(r.conviction, r.outcome) for r in scored if r.conviction is not None]
    return {
        "signal_type": SIGNAL_TYPE,
        "max_days": max_days,
        "n_signals": len(sigs),
        "n_scored": len(scored),
        "summary": summarize([r.outcome for r in scored]),
        "by_conviction": summarize_by_bucket(pairs) if pairs else {},
        "results": results,
    }
