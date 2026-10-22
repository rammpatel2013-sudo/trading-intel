"""Expiry clock — where we are in the OpEx / VIXpiration / quarterly cycle, with the read for each phase.

Pure functions, no I/O. Dates follow the exchange rules:
- Monthly SPX OpEx: third Friday of the month (AM-settled SET on the opening prints; if that Friday is a
  holiday, the Thursday before). Quarterly = Mar/Jun/Sep/Dec.
- VIX expiration ("VIXpiration"): the Wednesday that is 30 days before the NEXT month's third Friday
  (SOQ on the opening prints of the SPX strip); Tuesday if that Wednesday is a holiday.
- Weekly SPX: Mon/Tue/Wed/Thu/Fri PM-settled; the Friday of OpEx week is the AM monthly.

The "read" strings are the Alma / OptionsDepth framing (see claude/alma-methodology.md):
pre-OpEx the big strikes pin through charm and gamma; post-OpEx that support is gone and vol can
return; VIXpiration mornings tend to print soft (dealers sell long forward-vol hedges into the SOQ),
then 70-85 % of the vol-space dampening disappears; quarter-end adds pension rebalancing.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from trading_intel.timeutils import is_trading_session

_QUARTERLY = {3, 6, 9, 12}


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    while d.weekday() != 4:  # Friday
        d += timedelta(days=1)
    return d


def _prev_session(d: date) -> date:
    while not is_trading_session(d):
        d -= timedelta(days=1)
    return d


def monthly_opex(year: int, month: int) -> date:
    return _prev_session(third_friday(year, month))


def vix_expiry_for(year: int, month: int) -> date:
    """VIX expiration in `month`: 30 days before the following month's third Friday."""
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    d = third_friday(ny, nm) - timedelta(days=30)
    return _prev_session(d)


def _iter_months(start: date):
    y, m = start.year, start.month
    while True:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def next_events(today: date | None = None, horizon_days: int = 120) -> list[dict]:
    """Upcoming cycle events, soonest first: kind, date, days_to, label, read."""
    today = today or datetime.now().date()
    out = []
    for y, m in _iter_months(today):
        mo = monthly_opex(y, m)
        vx = vix_expiry_for(y, m)
        for kind, d in (("vixpiration", vx), ("opex", mo)):
            if d < today or (d - today).days > horizon_days:
                continue
            quarterly = kind == "opex" and m in _QUARTERLY
            out.append({
                "kind": "quarterly_opex" if quarterly else kind,
                "date": d.isoformat(),
                "days_to": (d - today).days,
                "label": (f"{'Quarterly ' if quarterly else 'Monthly '}OpEx {d:%a %b %d}" if kind == "opex"
                          else f"VIX expiration {d:%a %b %d}"),
            })
        if (mo - today).days > horizon_days and (vx - today).days > horizon_days:
            break
    out.sort(key=lambda e: e["days_to"])
    # month-end / quarter-end
    y, m = today.year, today.month
    last = (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)
    last = _prev_session(last)
    if last >= today:
        out.append({"kind": "quarter_end" if m in _QUARTERLY else "month_end", "date": last.isoformat(),
                    "days_to": (last - today).days,
                    "label": f"{'Quarter' if m in _QUARTERLY else 'Month'}-end {last:%a %b %d}"})
    out.sort(key=lambda e: e["days_to"])
    return out


def phase(today: date | None = None) -> dict:
    """Where we are: days since the last monthly OpEx and VIXpiration, next of each, and the read."""
    today = today or datetime.now().date()
    ev = next_events(today)
    nxt = {e["kind"]: e for e in ev if e["kind"] not in {}}
    opex = next((e for e in ev if e["kind"] in ("opex", "quarterly_opex")), None)
    vix = next((e for e in ev if e["kind"] == "vixpiration"), None)
    # last monthly opex
    y, m = (today.year, today.month)
    last_opex = monthly_opex(y, m)
    if last_opex > today:
        y2, m2 = (y - 1, 12) if m == 1 else (y, m - 1)
        last_opex = monthly_opex(y2, m2)
    since_opex = (today - last_opex).days
    # last VIXpiration (the Wednesday 30 days before NEXT month's OpEx)
    last_vix = vix_expiry_for(y, m)
    if last_vix > today:
        y2, m2 = (y - 1, 12) if m == 1 else (y, m - 1)
        last_vix = vix_expiry_for(y2, m2)
    since_vix = (today - last_vix).days
    reads = []
    if opex:
        if opex["days_to"] == 0:
            reads.append("OpEx day: the monthly legs settle on the opening prints (SET). Dealers' index hedge is "
                         "redundant once they settle, so the open meets responsive supply; after the SET only the "
                         "PM 0DTE book is left and charm then drives accelerating dealer buying by the hour.")
        elif opex["days_to"] <= 3:
            reads.append(f"OpEx in {opex['days_to']}d: expiring gamma pins price to the big strikes "
                         "(charm long above / short below the zero-speed line); the supportive flow leaves with the expiry.")
        elif opex["kind"] == "quarterly_opex" and opex["days_to"] <= 10:
            reads.append(f"Quarterly OpEx in {opex['days_to']}d: the JPM collar rolls; its vega supply disappears "
                         "at the reset and vol-control sells into it.")
    if since_opex in (1, 2, 3):
        reads.append(f"{since_opex}d after OpEx: the pin is gone, the 'window of risk' opens post-OpEx Tuesday "
                     "once Monday's buyback flow is done — vol can return.")
    elif 4 <= since_opex <= 10:
        reads.append(f"{since_opex}d after the {last_opex.isoformat()} OpEx: the monthly pin is gone and the book is "
                     "lighter, so trends travel further on less flow — the week's book and the 0DTE chain set the "
                     "levels, not the monthlies.")
    if since_vix in (1, 2):
        reads.append(f"VIXpiration was {since_vix}d ago ({last_vix.isoformat()}): the vol-space dampening from the VIX "
                     "call book is gone and the hedge buybacks in ES have run — spot now answers to the SPX book alone.")
    if vix:
        if vix["days_to"] == 0:
            reads.append("VIXpiration: SOQ on the opening SPX strip. Mornings tend to print soft as dealers sell their "
                         "long forward-vol hedges into the auction; after the SOQ most of the vol-space dampening is gone.")
        elif vix["days_to"] == 1:
            reads.append("VIXpiration tomorrow: VIX call dealers unwind — expect suppressive flow into the SOQ, "
                         "then hedge buybacks in ES.")
    if not reads:
        reads.append("Mid-cycle: no expiry flow in play; the dealer book and the tape set the structure.")
    return {"today": today.isoformat(), "since_opex_days": since_opex, "last_opex": last_opex.isoformat(),
            "since_vix_days": since_vix, "last_vix": last_vix.isoformat(),
            "next_opex": opex, "next_vix": vix, "events": ev[:6], "reads": reads}
