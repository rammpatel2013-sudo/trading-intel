"""MCP tools for the desk brief (2026-11-14). Read / write the NAS DB only - no vendor calls.

* ``get_desk_brief``      - the evening brief (top pick, two-leg names, groups, what matters, desk read)
* ``get_desk_symbol``     - one stock's dark-pool ratios, options split and gamma over recent sessions
* ``get_desk_scorecard``  - how our picks and TailThatWagsDog's picks did (5 / 10 / 20 sessions)
* ``record_ttwd_picks``   - save one of his published briefs for the paper track (follow him blindly)

Wire in server.py:
    from trading_intel.mcp import desk_tools
    desk_tools.register(mcp, settings, session_factory)
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.desk import metrics as m
from trading_intel.desk import store
from trading_intel.memory.models_desk import DeskGammaDaily, DeskUndDaily


def desk_brief(s: Session, day: str = "") -> dict[str, Any]:
    row = store.load_brief(s, date.fromisoformat(day) if day else None)
    if row is None:
        return {
            "error": "no desk brief stored yet (desk_brief runs 19:00 ET after desk_snapshot 17:15)"
        }
    p = dict(row.payload or {})
    slim = lambda rs: [
        {
            k: r.get(k)
            for k in ("symbol", "group", "d1", "d5", "bsr", "bull", "st_gex", "gex_vol", "dark")
        }  # noqa: E731
        for r in rs or []
    ]
    return {
        "date": str(row.date),
        "page": "http://192.168.1.211:8899/desk_brief.html",
        "coverage": p.get("coverage"),
        "totals": p.get("totals"),
        "top_pick": p.get("top_pick"),
        "hold_weaken": p.get("hold_weaken"),
        "two_leg": slim(p.get("two_leg")),
        "div_a": p.get("div_a"),
        "div_b": p.get("div_b"),
        "scoreboard": [
            {k: b.get(k) for k in ("group", "n", "avg1", "med1", "n2", "avg5")}
            for b in p.get("scoreboard") or []
        ],
        "etf_prints": p.get("etf_prints"),
        "leaders": slim(p.get("leaders")),
        "cross": p.get("cross"),
        "matters": p.get("matters"),
        "desk_read": p.get("desk_read"),
        "screen": [r.get("symbol") for r in p.get("screen") or []],
    }


def desk_symbol(s: Session, symbol: str, sessions: int = 20) -> dict[str, Any]:
    sym = symbol.upper().strip()
    end = date.today()
    dates, fin = store.load_finra(s, end, [sym], n=sessions + 10)
    series = fin.get(sym, [])
    dark = []
    for i, d in enumerate(dates[:sessions]):
        win = m.dark_windows(series[i : i + 10]) if i < len(series) else {}
        dark.append(
            {"date": str(d), **{k: win.get(k) for k in ("d1", "w1", "w2", "trend", "pass")}}
        )
    und = (
        s.execute(
            select(DeskUndDaily)
            .where(DeskUndDaily.symbol == sym)
            .order_by(DeskUndDaily.date.desc())
            .limit(sessions)
        )
        .scalars()
        .all()
    )
    gam = (
        s.execute(
            select(DeskGammaDaily)
            .where(DeskGammaDaily.symbol == sym)
            .order_by(DeskGammaDaily.date.desc())
            .limit(sessions)
        )
        .scalars()
        .all()
    )
    bars = store.load_bars(s, [sym], end - timedelta(days=40)).get(sym, [])
    closes = [b[4] for b in bars]
    d1, d5 = m.changes(closes) if closes else (None, None)
    last = bars[-1] if bars else None
    brief = store.load_brief(s)
    roles = []
    if brief is not None:
        p = brief.payload or {}
        roles = [
            k
            for k in ("two_leg", "leaders", "screen")
            if any(r.get("symbol") == sym for r in p.get(k) or [])
        ]
        roles += [k for k in ("div_a", "div_b") if sym in (p.get(k) or [])]
        if (p.get("top_pick") or {}).get("symbol") == sym:
            roles.insert(0, "top_pick")
    return {
        "symbol": sym,
        "last_bar": str(last[0]) if last else None,
        "d1": d1,
        "d5": d5,
        "bsr": m.bsr(last[2], last[3], last[4]) if last else None,
        "dark_pool": dark,
        "options": [
            {
                "date": str(u.date),
                "option_volume": u.option_volume,
                "bullish_pct": m.bullish_pct(u.call_buy, u.call_sell, u.put_buy, u.put_sell),
            }
            for u in und
        ],
        "gamma": [{"date": str(g.date), "st_gex": g.st_gex, "total_gex": g.total_gex} for g in gam],
        "in_latest_brief": {"date": str(brief.date) if brief else None, "roles": roles},
        "note": "dark ratio = FINRA short / (total - short); >1 on 1W and 2W and rising = dark leg passes. "
        "options bullish = (calls bought + puts sold) / split volume, all expiries. Gamma naive sign.",
    }


def desk_scorecard(s: Session) -> dict[str, Any]:
    from trading_intel.scheduler.jobs.desk_brief import scorecard

    brief = store.load_brief(s)
    day = brief.date if brief else date.today()
    return {
        "asof": str(day),
        **scorecard(s, day),
        "rules": "entry = next session open after the brief date; exit = close after 5 / 10 / 20 sessions; "
        "excess = vs SPY same window; beat = % of picks above SPY; no stops, equal weight.",
    }


def record_ttwd(
    s: Session,
    brief_date: str,
    top_pick: str,
    two_leg: Sequence[str],
    div_a: Sequence[str],
    div_b: Sequence[str],
    screen: Sequence[str],
    note: str = "",
) -> dict[str, Any]:
    d = date.fromisoformat(brief_date)
    picks: list[tuple[str, str, dict | None]] = []
    if top_pick:
        picks.append((top_pick, "top", {"note": note} if note else None))
    picks += [(x, "two_leg", None) for x in two_leg]
    picks += [(x, "div_a", None) for x in div_a]
    picks += [(x, "div_b", None) for x in div_b]
    picks += [(x, "screen", None) for x in screen]
    n = store.save_picks(
        s, d, "ttwd", [(x.strip().upper(), r, i) for x, r, i in picks if x and x.strip()]
    )
    return {
        "saved": n,
        "brief_date": brief_date,
        "scored": "5 / 10 / 20 sessions after the next open - see get_desk_scorecard",
    }


def register(
    mcp: Any, settings: Any, session_factory: sessionmaker[Session] | None
) -> None:  # noqa: ANN401
    if session_factory is None:
        return

    @mcp.tool()
    def get_desk_brief(day: str = "") -> dict[str, Any]:
        """Evening desk brief (TTWD-style): oversold names bouncing, dark-pool + options agreement, top pick."""
        with session_factory() as s:
            return desk_brief(s, day)

    @mcp.tool()
    def get_desk_symbol(symbol: str, sessions: int = 20) -> dict[str, Any]:
        """One stock: dark-pool buy ratio 1D/1W/2W, options bullish %, short-term gamma, 1D/5D, brief roles."""
        with session_factory() as s:
            return desk_symbol(s, symbol, sessions)

    @mcp.tool()
    def get_desk_scorecard() -> dict[str, Any]:
        """How our desk-brief picks and TailThatWagsDog's picks did after 5 / 10 / 20 sessions vs SPY."""
        with session_factory() as s:
            return desk_scorecard(s)

    @mcp.tool()
    def record_ttwd_picks(
        brief_date: str,
        top_pick: str,
        two_leg: list[str],
        div_a: list[str],
        div_b: list[str],
        screen: list[str],
        note: str = "",
    ) -> dict[str, Any]:
        """Save one TailThatWagsDog Equity Desk Brief (session date YYYY-MM-DD) for the paper track."""
        with session_factory() as s:
            return record_ttwd(s, brief_date, top_pick, two_leg, div_a, div_b, screen, note)
