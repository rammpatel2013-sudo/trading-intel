"""Phase 5 MCP tools (2026-11-04): Vol EOD tab reads, beta reset, expiry flow, swing scorecard.

All read the NAS DB (or build a DB-only report) — zero vendor calls. Descriptors
only (rule 4). Registered from ``server.build_server`` via ``register``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


def _rows(session: Session, sql: str, **kw: Any) -> list[dict]:
    out = []
    for r in session.execute(text(sql), kw).mappings().all():
        out.append({k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(r).items()})
    return out


def vix_overshoot(session: Session, days: int = 20) -> dict[str, Any]:
    rows = _rows(
        session, "SELECT * FROM vix_signal_snapshots ORDER BY date DESC LIMIT :n", n=max(1, days)
    )
    flags = _rows(
        session,
        "SELECT date, residual, vix_actual, spx_ret_pct FROM vix_signal_snapshots "
        "WHERE flag ORDER BY date DESC LIMIT 15",
    )
    return {
        "found": bool(rows),
        "latest": rows[0] if rows else None,
        "recent": rows,
        "recent_flags": flags,
        "rule": "residual = VIX - (VIX[-1] + a + b*SPX log% move), 126-session OLS on prior days; flag > +2 pts",
    }


def beta_reset(session: Session, days: int = 20) -> dict[str, Any]:
    from trading_intel.market.beta_reset import BetaResetRow, plain_read

    rows = _rows(
        session, "SELECT * FROM beta_reset_daily ORDER BY date DESC LIMIT :n", n=max(1, days)
    )
    if not rows:
        return {"found": False}
    r0 = rows[0]
    br = BetaResetRow(
        **{
            k: (date.fromisoformat(v) if k in ("date", "episode_start") and v else v)
            for k, v in r0.items()
        }
    )
    return {"found": True, "latest": r0, "read": plain_read(br), "recent": rows}


def register(mcp: Any, settings: Any, session_factory: sessionmaker[Session]) -> None:
    @mcp.tool()
    def get_vix_overshoot(days: int = 20) -> dict[str, Any]:
        """VIX overshoot (Vol tab V1): did VIX move more than the day's SPX move explains?

        Rolling 6-month OLS of daily VIX change on SPX log % move, fitted on prior
        sessions only; residual > +2 vol points = FLAG (fear bid beyond the tape).
        Reads ``vix_signal_snapshots`` (nightly vol_tab job). Descriptor only.
        """
        with session_factory() as session:
            return vix_overshoot(session, days)

    @mcp.tool()
    def get_beta_reset(days: int = 20) -> dict[str, Any]:
        """Beta-reset monitor (B1): has low beta (SPLV) broken, and is high beta (SPHB) holding vs its frozen beta?

        States: low beta intact/breaking/broken (absolute, relative, correlation checks)
        x high beta holding/lagging; RESET when broken + holding. Includes COR10D/COR90D
        correlation skew. Reads ``beta_reset_daily`` (nightly beta_reset job).
        """
        with session_factory() as session:
            return beta_reset(session, days)

    @mcp.tool()
    def generate_expiry_flow_report(symbol: str, days: int = 5) -> dict[str, Any]:
        """Expiry-flow term view (E1) for one ticker -> HTML + summary.

        Per DTE bucket (0-7 / 8-30 / 31-60 / 60+): bullish vs bearish premium from
        ``tas_daily_contract`` (last ``days`` sessions), call/put OI, and OI-change x
        IV-change (buyers vs writers opening). DB only — zero Convex calls.
        """
        from trading_intel.reports import build_expiry_flow

        path, summary = build_expiry_flow(symbol, days=days, settings=settings)
        return {"path": str(path), "uri": Path(path).as_uri(), "found": True, "summary": summary}

    @mcp.tool()
    def generate_swing_scorecard(symbols: list[str] | None = None) -> dict[str, Any]:
        """Swing scorecard (P5): 9-gate card per ticker from banked swing_features; flag at >= 6/9.

        Gates: trend, RSI band, IV/RV, IV rank, skew pct, DEX lean, GEX pct, no earnings
        in 21d, Stage-1 >= 60. Grey (no data) never counts. DB only. Ranking, not a signal.
        """
        from trading_intel.reports import build_swing_scorecard

        path, summary = build_swing_scorecard(symbols, settings=settings)
        return {
            "path": str(path),
            "uri": Path(path).as_uri(),
            "found": True,
            "flagged": [s for s in summary if s["flagged"]],
            "cards": summary,
        }

    @mcp.tool()
    def generate_swing_backtest(years: int = 2) -> dict[str, Any]:
        """Swing gates backtest (P6): do price-only gates (trend + momentum) beat leaned days? PASS/FAIL on a 30% holdout.

        quotes_daily closes only. PASS = holdout 20-session hit rate +5 pts over base and
        higher mean, >= 150 non-overlapping gated obs -> the P4 generator can drop `experimental`.
        """
        from trading_intel.reports import build_swing_backtest

        path, res = build_swing_backtest(years=years, settings=settings)
        return {
            "path": str(path),
            "uri": Path(path).as_uri(),
            "verdict": res.get("verdict"),
            "why": res.get("why"),
            "result": res,
        }
