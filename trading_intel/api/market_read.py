"""Market synthesis reader — fetch the four pillars and fuse them into one read.

Thin assembler: pulls the dealer-positioning ([[positioning]]), breadth ([[breadth]]),
VIX-complex (``get_vix``), and newsletter-signals ([[newsletter]]) readers and hands
them to the pure ``synthesis.market_read.build_read`` brain. Each source is
best-effort — a missing/empty one degrades that pillar to "n/a" rather than
crashing the read. Descriptor only (rule 4).
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from trading_intel.synthesis.market_read import build_read


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001 — one missing pillar shouldn't kill the read
        return None


def _norseman_state(session: Session) -> dict[str, Any] | None:
    """Latest banked NORSEMAN_STATE payload (written by strategies.norseman_regime)."""
    from sqlalchemy import select

    from trading_intel.memory.models import Signal

    row = session.execute(
        select(Signal.payload).where(Signal.signal_type == "NORSEMAN_STATE")
        .order_by(Signal.ts.desc()).limit(1)
    ).scalar_one_or_none()
    return row if isinstance(row, dict) else None


def build_market_read(session: Session, *, symbol: str = "SPX") -> dict[str, Any]:
    """Assemble + fuse the four pillars for ``symbol`` (default the SPX index)."""
    from trading_intel.api.breadth import build_breadth
    from trading_intel.api.newsletter import build_newsletter_signals
    from trading_intel.api.positioning import build_positioning
    from trading_intel.mcp.extra_tools import get_vix

    pos = _safe(lambda: build_positioning(session, symbol)) or {}
    breadth = _safe(lambda: build_breadth(session)) or {}
    news = _safe(lambda: build_newsletter_signals(session)) or {}
    vix = _safe(lambda: get_vix(session, days=5)) or {}
    vol = (vix.get("summary") or {}) if isinstance(vix, dict) else {}

    nm = _safe(lambda: _norseman_state(session)) or {}
    read = build_read(pos, breadth, vol, news, nm)
    read["symbol"] = symbol
    read["as_of"] = pos.get("as_of") or breadth.get("as_of")
    return read
