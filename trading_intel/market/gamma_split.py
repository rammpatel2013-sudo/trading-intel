"""Tao T3: why did dealer gamma change since yesterday? (2026-11-13 overnight)

Split the change in net gamma at spot between two EOD books into
  spot walk     = yesterday's book re-priced at today's spot minus at yesterday's spot,
  book change   = today's book minus yesterday's book, both at today's spot (new positions + roll-off),
plus the share of today's gamma that sits in the NEXT expiry and the projected book after it
expires (same spot). The projection is checked against the next book at the same spot (forecast
error tracked; Simon Ree missed -1.0 vs -6.6 on 9/18). All from banked chains via get_profile.
Descriptor only (rule 4).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session


def _at_spot(p: dict) -> tuple[float | None, dict[str, float]]:
    pr = p.get("profiles") or {}
    ref, g = pr.get("spot_ref") or [], pr.get("gamma") or {}
    if not ref or not g.get("all"):
        return None, {}
    i = len(ref) // 2
    return float(g["all"][i]), {k: float(v[i]) for k, v in (g.get("by_expiry") or {}).items()}


def split(session: Session, symbol: str = "SPX", *, today: date | None = None,
          prev: date | None = None, profile=None) -> dict[str, Any]:
    if profile is None:
        from trading_intel.mcp.profile_tool import get_profile as profile
    pt = profile(session, symbol, as_of=today, greeks=("gamma",))
    if not pt.get("found"):
        return {"found": False, "reason": "no book"}
    t_day = date.fromisoformat(pt["as_of"])
    st = float(pt["spot"])
    pp = profile(session, symbol, as_of=prev or date.fromordinal(t_day.toordinal() - 1), greeks=("gamma",))
    if not pp.get("found") or pp["as_of"] == pt["as_of"]:
        return {"found": False, "reason": "no earlier book"}
    pps = profile(session, symbol, as_of=date.fromisoformat(pp["as_of"]), spot_ref=st, greeks=("gamma",))
    g_t, by_t = _at_spot(pt)
    g_p, _ = _at_spot(pp)
    g_ps, _ = _at_spot(pps)
    if None in (g_t, g_p, g_ps):
        return {"found": False, "reason": "empty profile"}
    nxt = min(by_t, key=lambda k: k) if by_t else None
    nxt_g = by_t.get(nxt, 0.0) if nxt else 0.0
    gross = sum(abs(v) for v in by_t.values()) or None
    return {
        "found": True, "symbol": symbol, "book": pt["as_of"], "prev_book": pp["as_of"],
        "spot": st, "prev_spot": float(pp["spot"]),
        "gamma_now": g_t, "gamma_prev": g_p, "change": g_t - g_p,
        "spot_walk": g_ps - g_p, "book_change": g_t - g_ps,
        "next_expiry": nxt, "next_expiry_gamma": nxt_g,
        "next_expiry_share": (abs(nxt_g) / gross) if gross else None,
        "projected_after_next": g_t - nxt_g, "units": "$bn per 1% move",
    }


def plain(s: dict[str, Any]) -> str:
    if not s.get("found"):
        return f"Gamma change: {s.get('reason', 'no data')}."
    w = lambda v: f"{v:+.2f}"  # noqa: E731
    return (f"Dealer gamma at {s['spot']:,.0f}: {s['gamma_prev']:+.2f} -> {s['gamma_now']:+.2f} ($bn per 1%). "
            f"Of the {w(s['change'])} change, {w(s['spot_walk'])} came from price moving and {w(s['book_change'])} "
            f"from positions (new trades + expiries). The next expiry ({s['next_expiry']}) holds "
            f"{(s['next_expiry_share'] or 0):.0%} of the book; after it expires the book would read "
            f"{s['projected_after_next']:+.2f} at this price.")
