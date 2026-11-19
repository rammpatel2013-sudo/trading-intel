"""card_tools.py - created 2026-11-14. MCP tools for the SPX 0DTE map card (doc claude/card-visual-grammar-20261114.md).

    get_odte_card       - the card's data: regime, both switches, cap / floor / steps / air pockets from open
                          interest, straddle, 1-sigma, 50/70/80% close ranges, the three-way close odds, if/then
                          rows, the checks (one book, one spot, smooth odds) and the SOURCE of every number.
    generate_odte_card  - the same, written as the phone page (scripts/odte_card_report.py render) under
                          reports/odte_card/; returns the path. No Telegram from here.

One book + one spot for every number (market.book_spot). Descriptor, not a signal (rule 4).
"""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from typing import Any

from trading_intel.timeutils import eastern_now

REPO = Path(__file__).resolve().parents[2]


def _render(card: dict[str, Any]) -> str:
    spec = importlib.util.spec_from_file_location("odte_card_report", REPO / "scripts" / "odte_card_report.py")
    mod = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
    spec.loader.exec_module(mod)                          # type: ignore[union-attr]
    return mod.render(card)


def register(mcp: Any, settings: Any, session_factory: Any) -> None:  # noqa: ANN401
    """Wire the card tools into the MCP server (called from server.py)."""
    from trading_intel.market.odte_card import build_odte_card

    def _d(x: str | None) -> date | None:
        return date.fromisoformat(x) if x else None

    @mcp.tool()
    def get_odte_card(symbol: str = "SPX", as_of: str | None = None) -> dict[str, Any]:
        """SPX 0DTE map card data from ONE options book and ONE spot: regime (spot vs the 0DTE and board gamma
        switches), cap / floor / steps / air pockets (open interest, next 7 days), straddle, 1-sigma, 50/70/80%
        close ranges and the three-way close odds (smoothed smile), plain if/then rows, the checks, and
        ``sources`` = where each number came from. ``as_of`` YYYY-MM-DD = that day's book. Descriptor only."""
        with session_factory() as session:
            return build_odte_card(session, symbol, as_of=_d(as_of))

    @mcp.tool()
    def generate_odte_card(symbol: str = "SPX", as_of: str | None = None) -> dict[str, Any]:
        """Write the SPX 0DTE map card as a phone-readable page (no scripts) under reports/odte_card/ and
        return its path. Same data as get_odte_card. No Telegram (the NAS task sends it)."""
        with session_factory() as session:
            card = build_odte_card(session, symbol, as_of=_d(as_of))
        if not card.get("found"):
            return card
        out = REPO / "reports" / "odte_card"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"odte_card_{card['session']}_{eastern_now():%H%M}.html"
        path.write_text(_render(card), encoding="utf-8")
        return {"symbol": card["symbol"], "found": True, "path": str(path), "session": card["session"],
                "checks": card["checks"]}
