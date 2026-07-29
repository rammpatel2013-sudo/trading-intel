"""Defined-risk structure builder for the ⚡ combined read.

Given a name, an expiry, and two strikes (long/short), build a vertical call debit
spread and compute its economics — max risk capped at the debit, max gain, target
return, breakeven. The orchestrator picks strikes (usually the long = the strike the
smart-money buyer used, per :mod:`jaguar.parse`) and passes our chain's marks; this
module is pure and just does the arithmetic, so it is fully unit-tested.

These are illustrative constructions for the reader's own evaluation, never automated
signals or advice (FlashAlpha rule 4). When our chain marks aren't available the
economics degrade to ``None`` and the structure still renders with its strikes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Structure:
    """A vertical call spread with capped downside and its risk/reward."""

    label: str
    ticker: str
    expiry: str
    long_strike: float
    short_strike: float
    width: float
    debit: float | None
    max_risk: float | None
    max_gain: float | None
    target_pct: float | None
    breakeven: float | None
    rationale: str = ""


def _rr(width: float, debit: float | None, contracts: int) -> tuple[float | None, ...]:
    if debit is None or debit <= 0 or debit >= width:
        return None, None, None, None
    max_risk = round(debit * 100 * contracts, 2)
    max_gain = round((width - debit) * 100 * contracts, 2)
    target_pct = round(max_gain / max_risk, 3) if max_risk else None
    breakeven = round(debit, 2)  # added to long_strike by caller-friendly field below
    return max_risk, max_gain, target_pct, breakeven


def call_spread(
    ticker: str,
    expiry: str,
    long_strike: float,
    short_strike: float,
    *,
    long_price: float | None = None,
    short_price: float | None = None,
    contracts: int = 1,
    rationale: str = "",
) -> Structure:
    """A long call debit spread ``long_strike/short_strike``.

    ``long_price``/``short_price`` are our chain's marks; when either is missing the
    debit and downstream economics are ``None`` (the structure still renders). Max risk
    is the debit — the most that can be lost if the thesis doesn't work — sized for a
    strong multiple to the short strike (the "10%+ with capped loss" goal).
    """
    width = round(short_strike - long_strike, 4)
    debit = (
        round(long_price - short_price, 2)
        if (long_price is not None and short_price is not None)
        else None
    )
    max_risk, max_gain, target_pct, be_debit = _rr(width, debit, contracts)
    breakeven = round(long_strike + be_debit, 2) if be_debit is not None else None
    label = f"{ticker} {expiry} {long_strike:g}/{short_strike:g} call spread"
    return Structure(
        label=label,
        ticker=ticker,
        expiry=expiry,
        long_strike=long_strike,
        short_strike=short_strike,
        width=width,
        debit=debit,
        max_risk=max_risk,
        max_gain=max_gain,
        target_pct=target_pct,
        breakeven=breakeven,
        rationale=rationale,
    )


def short_strike_for_move(
    long_strike: float, *, target_move_pct: float = 0.20, step: float = 5.0
) -> float:
    """A short strike ~``target_move_pct`` above the long, rounded to the nearest ``step``.

    Keeps the spread wide enough to target a 10%+ move while capping cost — the width
    the reader is paying up to reach.
    """
    raw = long_strike * (1.0 + target_move_pct)
    return max(long_strike + step, round(raw / step) * step)
