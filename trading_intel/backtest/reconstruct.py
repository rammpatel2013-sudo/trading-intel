"""Rebuild historical EM-break re-entry cases from CVForge historical option OHLC.

Path (b): where we have no banked pre-earnings straddle (it only banks forward), pull
the day-before-earnings ATM call + put historical closes to reconstruct the implied
expected move, test whether the realized gap broke it, and — absent historical OI to
place the exact call wall — proxy the upside target at the expected-move level and
walk the underlying OHLC forward through the pure ``backtest.em_break`` engine.

The MATH here is pure + unit-tested. The FETCH (``load_cases``) is a thin, documented
wrapper over CVForge's historical-option endpoints; its field names must be confirmed
against a live probe before first use (same discipline as the ``earn_cal`` schema
check — see ``docs/em_break_backtest.md``). CVForge is the existing secondary
datasource on the same Convex backend (ADR-004), so this adds NO new vendor (rule 1).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from trading_intel.backtest.em_break import Bar, Outcome, evaluate_outcome


def straddle_from_legs(call_close: float, put_close: float) -> float:
    """ATM straddle price = ATM call close + ATM put close (in price units)."""
    return float(call_close) + float(put_close)


def em_pct(straddle: float, spot: float) -> float:
    """Implied expected move as a fraction of spot (``straddle / spot``)."""
    if spot <= 0:
        raise ValueError("spot must be > 0")
    return float(straddle) / float(spot)


def gap_pct(pre_close: float, post: float) -> float:
    """Post-earnings gap vs the pre-earnings close (signed fraction)."""
    if pre_close <= 0:
        raise ValueError("pre_close must be > 0")
    return float(post) / float(pre_close) - 1.0


def em_broke(gap: float, expected_move: float) -> bool:
    """Did the realized gap exceed the implied expected move (either direction)?"""
    return abs(gap) >= abs(expected_move)


def reconstruct_outcome(
    entry: float,
    straddle: float,
    forward: Sequence[Bar],
    *,
    target_mult: float = 1.0,
    stop_mult: float = 1.0,
    max_days: int = 20,
) -> Outcome | None:
    """Proxy re-entry structure from the straddle when historical OI is unavailable.

    Target = ``entry + target_mult * straddle`` (one expected-move up — near where a
    rebuilt call wall typically sits); stop = ``entry - stop_mult * straddle``. Both
    ``straddle`` and the walls are in price units. Delegates the path walk to the
    tested pure engine.
    """
    target = float(entry) + float(target_mult) * float(straddle)
    stop = float(entry) - float(stop_mult) * float(straddle)
    return evaluate_outcome(float(entry), target, stop, forward, max_days=max_days)


# ── Documented loader (confirm CVForge historical-option field names first) ────


def load_cases(
    cvforge: object,
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    target_dte: int = 30,
    max_days: int = 20,
) -> list[Outcome]:
    """Reconstruct cases over ``symbols`` between ``start`` and ``end`` (path b).

    Skeleton wiring — CONFIRM the CVForge historical-option response shape with a live
    probe (like the ``earn_cal`` schema check) before trusting the output; the field
    accessors are marked TODO. Per earnings date ``d`` for symbol ``S``:

      1. underlying = cvforge.hist_ohlc(S, start..end)          # date,open,high,low,close
      2. pre       = last underlying close on/before d-1
      3. atm       = strike nearest ``pre`` at the expiry ~``target_dte`` DTE after d
         call/put = cvforge.hist_option_ohlc(S, expiry, atm, 'C'/'P', d-1).close
         straddle = straddle_from_legs(call, put); move = em_pct(straddle, pre)
      4. gap = gap_pct(pre, first underlying close after d)
         if not em_broke(gap, move): continue
      5. entry = stabilization close (~OPEX after d) from underlying
         fwd   = Bars from underlying after the entry date (<= max_days)
         yield reconstruct_outcome(entry, straddle, fwd, max_days=max_days)

    Raises until the accessors are confirmed, so it can't silently emit garbage.
    """
    raise NotImplementedError(
        "load_cases: confirm CVForge historical-option field names via a live probe "
        "first (see docs/em_break_backtest.md), then wire the accessors marked TODO."
    )
