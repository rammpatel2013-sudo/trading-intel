"""Chain-history reader — the never-pruned ``oi_chain_daily`` roll-up for one name.

Pure DB read (no vendor calls). Descriptor only (rule 4).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import OiChainDaily

_FIELDS = (
    "spot", "call_oi", "put_oi", "call_volume", "put_volume", "call_oi_change",
    "put_oi_change", "net_gxoi", "call_gxoi", "put_gxoi", "net_dxoi", "net_vxoi",
    "call_wall", "call_wall_gxoi", "put_wall", "put_wall_gxoi", "atm_iv_30d", "n_contracts",
)


def build_chain_history(session: Session, symbol: str, *, days: int = 250) -> dict[str, Any]:
    sym = symbol.strip().upper()
    rows = session.execute(
        select(OiChainDaily).where(OiChainDaily.symbol == sym)
        .order_by(OiChainDaily.ts.desc()).limit(max(1, min(int(days), 5000)))
    ).scalars().all()
    if not rows:
        return {"symbol": sym, "found": False}
    series = [
        {"date": r.ts.isoformat(), **{f: getattr(r, f) for f in _FIELDS}}
        for r in reversed(rows)
    ]
    for p in series:
        c, q = p["call_oi"], p["put_oi"]
        p["pc_oi_ratio"] = (q / c) if c else None
    return {"symbol": sym, "found": True, "n": len(series), "series": series}
