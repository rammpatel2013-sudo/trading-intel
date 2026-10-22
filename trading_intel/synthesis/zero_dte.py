"""0DTE gamma map for the AM brief — today's-expiry flip, walls, net GEX sign, range.

Reads the 08:35 ``index_walls_am`` snapshot in ``oi_chain_eod`` (the only fresh
SPX per-strike chain before the 09:00 brief; ``get_live_gex`` is empty pre-open),
restricted to contracts expiring TODAY. Zero vendor calls.

* **walls** — biggest 0DTE call gamma-OI strike at/above spot, put at/below.
* **net 0DTE GEX** — calls +, puts − (the house convention). This is an INFERRED
  dealer book, not cleared inventory: read the sign/shape, not the level.
* **flip** — zero-crossing of the 0DTE-only gamma-by-spot profile nearest spot.
* **expected range** — spot × 0DTE ATM IV × √(1/252) (one session, 1σ).
* **burn-off** — front-expiry gamma phase from ``get_gamma_burnoff``.

Descriptor only (FlashAlpha rule 4). ``build_zero_dte`` never raises.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import GreeksSnapshot, OiChainEod

log = structlog.get_logger(__name__)


def _flip(grid: list[float], vals: list[float], spot: float) -> float | None:
    best = None
    for i in range(1, len(grid)):
        a, b = vals[i - 1], vals[i]
        if (a < 0 <= b) or (a > 0 >= b):
            x = grid[i - 1] + (grid[i] - grid[i - 1]) * (0 - a) / ((b - a) or 1e-12)
            if best is None or abs(x - spot) < abs(best - spot):
                best = x
    return best


def summarize(rows: list[tuple], spot: float, *, flip: float | None = None) -> dict[str, Any]:
    """Pure: rows = (strike, cp, gxoi, iv, oi) for TODAY's expiry → the map."""
    calls = [r for r in rows if str(r[1]).upper().startswith("C")]
    puts = [r for r in rows if str(r[1]).upper().startswith("P")]
    cw = max((r for r in calls if r[0] >= spot), key=lambda r: abs(r[2] or 0), default=None)
    pw = max((r for r in puts if r[0] <= spot), key=lambda r: abs(r[2] or 0), default=None)
    net = sum(abs(r[2] or 0) for r in calls) - sum(abs(r[2] or 0) for r in puts)
    atm = min(rows, key=lambda r: abs(r[0] - spot), default=None)
    iv = atm[3] if atm and atm[3] else None
    em = spot * iv * math.sqrt(1 / 252) if iv else None
    return {
        "spot": spot,
        "call_wall": cw[0] if cw else None,
        "put_wall": pw[0] if pw else None,
        "net_gex": net,
        "net_sign": "long γ (dampening)" if net > 0 else "short γ (amplifying)" if net < 0 else "flat",
        "flip": flip,
        "atm_iv": iv,
        "em": em,
        "range": (spot - em, spot + em) if em else None,
        "n_contracts": len(rows),
    }


def build_zero_dte(session: Session, symbol: str = "SPX", *, today: date | None = None) -> dict[str, Any] | None:
    """Today's-expiry gamma map, or None if no 0DTE chain is banked for today."""
    sym = symbol.upper()
    today = today or date.today()
    try:
        ts = session.execute(
            select(func.max(OiChainEod.ts)).where(OiChainEod.symbol == sym)
        ).scalar_one_or_none()
        if ts is None:
            return None
        rows = session.execute(
            select(OiChainEod.strike, OiChainEod.cp, OiChainEod.gxoi, OiChainEod.iv, OiChainEod.oi)
            .where(OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.expiry == today)
        ).all()
        if not rows:
            return None
        spot = session.execute(
            select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.spot.isnot(None))
            .order_by(GreeksSnapshot.ts.desc()).limit(1)
        ).scalar_one_or_none()
        if not spot:
            return None
        flip = None
        try:
            from trading_intel.mcp.profile_tool import get_profile

            prof = get_profile(session, sym, span=0.03, n_points=61, dte_max=0, greeks=("gamma",))
            p = prof.get("profiles") or {}
            g = (p.get("gamma") or {}).get("by_expiry", {}).get(today.isoformat())
            if g:
                flip = _flip([float(x) for x in p["spot_ref"]], [float(v) for v in g], float(spot))
        except Exception as exc:
            log.warning("zero_dte.flip_failed", error=str(exc))
        out = summarize([tuple(r) for r in rows], float(spot), flip=flip)
        try:
            from trading_intel.mcp.em_tools import get_gamma_burnoff

            bo = get_gamma_burnoff(session, sym)
            out["burnoff_phase"] = bo.get("phase") if bo.get("found") else None
            out["front_share"] = bo.get("front_share") if bo.get("found") else None
        except Exception as exc:
            log.warning("zero_dte.burnoff_failed", error=str(exc))
        out.update({"symbol": sym, "as_of": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "expiry": today.isoformat()})
        return out
    except Exception as exc:
        log.warning("zero_dte.failed", error=str(exc))
        return None
