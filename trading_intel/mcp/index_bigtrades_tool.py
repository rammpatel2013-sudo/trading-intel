"""MCP reader: biggest INDEX option prints off the stored market-wide tape.

Reads ``tas_prints`` rows tagged ``source='convex_index'`` (the big SPX/SPY/QQQ
prints the ``tas_capture_job`` un-excludes above the index premium floor) for a
session day, and returns them GROUPED BY STRUCTURE via the stored ``leg_group``
(legs the collector clustered in one poll — a vertical/fly/calendar/roll) with
the singletons as outrights. Each row carries the OBSERVED ``side`` and the
derived ``condition``/``is_sweep``/``is_block``/``is_financing`` tags.

IMPORTANT (rule 4 + the tape's reality): ``side`` is the per-leg aggressor, NOT
the trade's direction — a size-matched pair is one structure, and a roll can
print "sell" on both legs. Consumers must read ``leg_group`` structures, never
sum ``side``. READ-ONLY, descriptor only.

Wire in server.py:
    from trading_intel.mcp import index_bigtrades_tool as ibt
    @mcp.tool()
    def get_index_bigtrades(trade_date: str | None = None, limit: int = 60) -> dict[str, Any]:
        with session_factory() as session:
            return ibt.get_index_bigtrades(session, trade_date=trade_date, limit=limit)
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import TasPrint

_SOURCE = "convex_index"


def _row(p: TasPrint) -> dict[str, Any]:
    return {
        "ts": p.ts.isoformat() if p.ts else None,
        "symbol": p.symbol,
        "root": p.root,
        "expiry": p.expiry.isoformat() if p.expiry else None,
        "strike": p.strike,
        "cp": p.cp,
        "size": p.size,
        "notional": p.notional,
        "side": p.side,               # OBSERVED aggressor — NOT trade direction
        "condition": p.condition,
        "is_sweep": p.is_sweep,
        "is_block": p.is_block,
        "is_financing": p.is_financing,
        "leg_group": p.leg_group,
        "delta": p.delta,
        "iv": p.iv,
        "spot": p.spot,
    }


def get_index_bigtrades(
    session: Session,
    *,
    trade_date: str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    """Biggest stored index prints for a day, grouped into structures + outrights.

    Returns ``{as_of, source, n, structures:[{leg_group, legs:[...], premium}],
    outrights:[...]}``. ``found=False`` when the collector hasn't stored index
    prints yet (needs the ``tas_capture`` un-exclude live + migration 0041).
    """
    if trade_date:
        day: date | None = date.fromisoformat(trade_date)
    else:
        day = session.execute(
            select(func.max(TasPrint.trade_date)).where(TasPrint.source == _SOURCE)
        ).scalar()
    if day is None:
        return {"found": False, "source": _SOURCE, "reason": "no convex_index prints stored yet"}

    rows = session.execute(
        select(TasPrint)
        .where(TasPrint.source == _SOURCE, TasPrint.trade_date == day)
        .order_by(TasPrint.notional.desc())
        .limit(limit)
    ).scalars().all()
    if not rows:
        return {"found": False, "as_of": day.isoformat(), "source": _SOURCE}

    # group by leg_group (a real structure); NULL/empty = standalone outright
    groups: dict[str, list[TasPrint]] = {}
    outrights: list[dict[str, Any]] = []
    for p in rows:
        if p.leg_group:
            groups.setdefault(p.leg_group, []).append(p)
        else:
            outrights.append(_row(p))

    structures: list[dict[str, Any]] = []
    for lg, legs in groups.items():
        if len(legs) < 2:  # a singleton that happened to carry a group id → treat as outright
            outrights.append(_row(legs[0]))
            continue
        legs_sorted = sorted(legs, key=lambda x: (x.expiry or date.min, x.strike or 0.0))
        structures.append({
            "leg_group": lg,
            "premium": round(sum(l.notional or 0.0 for l in legs), 2),
            "n_legs": len(legs),
            "expiries": sorted({l.expiry.isoformat() for l in legs if l.expiry}),
            "strikes": sorted({l.strike for l in legs if l.strike is not None}),
            "financing": any(l.is_financing for l in legs),
            "legs": [_row(l) for l in legs_sorted],
        })
    structures.sort(key=lambda s: s["premium"], reverse=True)
    outrights.sort(key=lambda o: o.get("notional") or 0.0, reverse=True)

    return {
        "found": True,
        "as_of": day.isoformat(),
        "source": _SOURCE,
        "n": len(rows),
        "n_structures": len(structures),
        "n_outrights": len(outrights),
        "structures": structures,
        "outrights": outrights,
        "note": "side is per-leg aggressor, NOT trade direction; read leg_group structures.",
    }
