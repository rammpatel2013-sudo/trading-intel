"""Gamma roll-off: how much of the book expires this week, and what's left (§7).

@GammaGirlLab's weekly positioning page carries a "net Γ at spot by day as the
dailies roll off" panel — front-loaded, then fading. This reproduces it from
the per-expiration GEX term structure the system already banks, so there is no
new maths and no new vendor call: feed it ``get_gex_term``'s ``term`` rows.

Two readings come out of it:

* **the step-down** — net gamma remaining after each expiry this week settles,
  which is what makes a pin loosen as the week runs out;
* **concentration** — the single biggest expiry's share. One giant pole on the
  monthly (she measured 81% of a week's book once) means the grip tightens into
  that Friday and releases straight after.

Pure functions, no I/O. Descriptor only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Iterable

from trading_intel.expiry_clock import monthly_opex


def week_end(today: date) -> date:
    """The Friday of ``today``'s week (``today`` itself if it is a Friday)."""
    return today + timedelta(days=(4 - today.weekday()) % 7)


def is_monthly_opex(d: date) -> bool:
    """Is ``d`` the monthly SPX OpEx (the third Friday)?"""
    return d == monthly_opex(d.year, d.month)


def build_rolloff(
    term: Iterable[dict[str, Any]], *, today: date | None = None
) -> dict[str, Any]:
    """Roll-off profile for the current week from per-expiration GEX rows.

    ``term`` rows are ``{"expiration": "YYYY-MM-DD", "dte": int, "gex": float}``
    as ``get_gex_term`` returns them. Rows with no gex or no expiration are
    dropped; a row whose expiration has already passed is ignored.
    """
    today = today or date.today()
    end = week_end(today)

    rows: list[tuple[date, float]] = []
    for r in term or []:
        exp, gex = _as_date(r.get("expiration")), r.get("gex")
        if exp is None or gex is None or exp < today:
            continue
        try:
            rows.append((exp, float(gex)))
        except (TypeError, ValueError):
            continue
    if not rows:
        return {"found": False, "steps": [], "total": None, "biggest": None,
                "week_end": end.isoformat(), "is_opex": is_monthly_opex(today)}

    rows.sort()
    total = sum(g for _, g in rows)
    gross = sum(abs(g) for _, g in rows) or 1.0

    steps: list[dict[str, Any]] = []
    remaining = total
    for exp, gex in rows:
        if exp > end:
            break
        remaining -= gex
        steps.append(
            {
                "expiration": exp.isoformat(),
                "dte": (exp - today).days,
                "gex": gex,
                "share": abs(gex) / gross,
                "remaining_after": remaining,
            }
        )

    big_exp, big_gex = max(rows, key=lambda kv: abs(kv[1]))
    week_gross = sum(abs(s["gex"]) for s in steps)
    return {
        "found": True,
        "today": today.isoformat(),
        "week_end": end.isoformat(),
        "is_opex": is_monthly_opex(today),
        "total": total,
        "gross": gross,
        "steps": steps,
        "week_share": week_gross / gross,
        "biggest": {
            "expiration": big_exp.isoformat(),
            "dte": (big_exp - today).days,
            "gex": big_gex,
            "share": abs(big_gex) / gross,
            "is_this_week": big_exp <= end,
        },
        "expiring_today": next(
            (s for s in steps if s["dte"] == 0), None
        ),
    }


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None
