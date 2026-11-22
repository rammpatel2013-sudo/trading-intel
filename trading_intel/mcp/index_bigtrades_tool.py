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
        "price": p.price,
    }



#: rows read before merging; the merged list is cut to ``limit`` afterwards.
_FETCH = 3000
#: legs of one structure must trade within this size ratio (else they are separate trades).
_SIZE_MATCH = 0.75


def _fin(p: TasPrint) -> bool:
    """Financing recomputed with the current rule (stored tags before 2026-11-21 used |delta| >= 0.85)."""
    from trading_intel.scheduler.jobs.tas_capture_job import _is_financing

    dte = (p.expiry - p.trade_date).days if (p.expiry and p.trade_date) else None
    return _is_financing(p.cp, p.delta, dte)


def _blocks(rows: list[TasPrint]) -> list[dict[str, Any]]:
    """Merge prints of the SAME contract in the SAME second at the same price into one block.

    2026-11-21: the 11/17 13,113-lot Nov 20 7,500P sale printed as 8 pieces (5,200 / 1,300 / ...)
    across 4 leg groups - each piece alone looked small or got glued to an unrelated leg.
    """
    out: dict[tuple, dict[str, Any]] = {}
    for p in rows:
        sec = p.ts.replace(microsecond=0) if p.ts else None
        key = (p.symbol, sec, round(p.price or 0.0, 2))
        b = out.get(key)
        if b is None:
            b = _row(p) | {"prints": 0, "size": 0, "notional": 0.0, "leg_groups": set()}
            b["is_financing"] = _fin(p)
            out[key] = b
        b["prints"] += 1
        b["size"] += p.size or 0
        b["notional"] += p.notional or 0.0
        if p.leg_group:
            b["leg_groups"].add(p.leg_group)
    return list(out.values())


def _structures(blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Blocks sharing a stored leg_group form one structure if their sizes match; else outrights."""
    parent = list(range(len(blocks)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[str, int] = {}
    for i, b in enumerate(blocks):
        for lg in b["leg_groups"]:
            if lg in owner:
                parent[find(i)] = find(owner[lg])
            else:
                owner[lg] = i
    comps: dict[int, list[dict[str, Any]]] = {}
    for i, b in enumerate(blocks):
        comps.setdefault(find(i), []).append(b)

    structures: list[dict[str, Any]] = []
    outrights: list[dict[str, Any]] = []
    for legs in comps.values():
        sizes = [l["size"] for l in legs]
        if len(legs) < 2 or min(sizes) < _SIZE_MATCH * max(sizes):
            outrights.extend(legs)
            continue
        legs_sorted = sorted(legs, key=lambda x: (x["expiry"] or "", x["strike"] or 0.0))
        cps = {(l["expiry"], l["strike"], l["cp"]) for l in legs}
        box = any((e, k, "C") in cps and (e, k, "P") in cps for e, k, _ in cps)
        structures.append({
            "leg_group": sorted({g for l in legs for g in l["leg_groups"]})[0],
            "premium": round(sum(l["notional"] for l in legs), 2),
            "n_legs": len(legs),
            "expiries": sorted({l["expiry"] for l in legs if l["expiry"]}),
            "strikes": sorted({l["strike"] for l in legs if l["strike"] is not None}),
            "financing": box or all(l["is_financing"] for l in legs),
            "legs": legs_sorted,
        })
    return structures, outrights


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
    Same-second prints of one contract are merged first; financing (box / synthetic / deep-ITM
    long-dated) structures and outrights are listed AFTER real flow, so they can't crowd it out.
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
        .limit(max(_FETCH, limit))
    ).scalars().all()
    if not rows:
        return {"found": False, "as_of": day.isoformat(), "source": _SOURCE}

    structures, outrights = _structures(_blocks(rows))
    for x in structures:
        for l in x["legs"]:
            l["leg_groups"] = sorted(l["leg_groups"])
    for o in outrights:
        o["leg_groups"] = sorted(o["leg_groups"])
    # real flow first, financing after; each by premium
    structures.sort(key=lambda s: (s["financing"], -s["premium"]))
    outrights.sort(key=lambda o: (bool(o["is_financing"]), -(o.get("notional") or 0.0)))
    n_items = max(1, limit)
    structures = structures[:n_items]
    outrights = outrights[:n_items]

    return {
        "found": True,
        "as_of": day.isoformat(),
        "source": _SOURCE,
        "n": len(rows),
        "n_structures": len(structures),
        "n_outrights": len(outrights),
        "structures": structures,
        "outrights": outrights,
        "note": ("side is per-leg aggressor, NOT trade direction; read leg_group structures. "
                 "Same-second prints of one contract are merged (prints = pieces); "
                 "financing items listed after real flow."),
    }
