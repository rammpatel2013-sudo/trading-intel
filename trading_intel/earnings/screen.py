"""Build the earnings-week signal-alignment screen (angle x flow x EPS revision).

Ports Fahad/Jaguar's weekend process: take this week's earnings reporters, keep
those that carry a research ANGLE (watchlist sentiment) and cross-reference the
options FLOW (our tape roll-up) and the EPS-estimate REVISION, then rank on
alignment via ``earnings.alignment``. Reads banked data only — no I/O to
vendors, no writes (FlashAlpha rule 4). Shared by the scheduled job and the MCP
tool so both return the identical screen.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.earnings.alignment import AlignmentInputs, score_alignment
from trading_intel.mcp.em_tools import get_earnings_calendar
from trading_intel.mcp.extra_tools import get_flow_scorecard
from trading_intel.memory.models import EstimateSnapshot, WatchlistEntry


def _reporters(session: Session, days: int) -> dict[str, dict[str, Any]]:
    cal = get_earnings_calendar(session, days=days)
    out: dict[str, dict[str, Any]] = {}
    for e in cal.get("events") or []:
        sym = (e.get("symbol") or "").upper()
        if sym and sym not in out:
            out[sym] = {"date": e.get("date"), "session": e.get("session")}
    return out


def _angles(session: Session) -> dict[str, dict[str, Any]]:
    """Confidence-weighted mean sentiment per symbol from the research watchlist."""
    rows = session.execute(
        select(
            WatchlistEntry.symbol,
            WatchlistEntry.sentiment,
            WatchlistEntry.confidence,
            WatchlistEntry.themes,
        ).where(WatchlistEntry.active.is_(True))
    ).all()
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.sentiment is None:
            continue
        sym = (r.symbol or "").upper()
        w = float(r.confidence) if r.confidence is not None else 0.6
        d = agg.setdefault(sym, {"sw": 0.0, "w": 0.0, "themes": r.themes})
        d["sw"] += float(r.sentiment) * w
        d["w"] += w
    return {
        s: {
            "angle": (d["sw"] / d["w"]) if d["w"] else None,
            "confidence": min(1.0, d["w"] / 2.0),
            "themes": d["themes"],
        }
        for s, d in agg.items()
    }


def _flows(session: Session) -> dict[str, dict[str, Any]]:
    sc = get_flow_scorecard(session, lookback_days=20, min_notional=500_000.0, limit=200)
    return {(r.get("root") or "").upper(): r for r in (sc.get("rows") or [])}


def _revisions(session: Session, syms: list[str]) -> dict[str, float]:
    """EPS revision fraction = latest eps_avg vs the prior (>=7d older) snapshot."""
    out: dict[str, float] = {}
    for sym in syms:
        rows = session.execute(
            select(EstimateSnapshot.ts, EstimateSnapshot.eps_avg)
            .where(EstimateSnapshot.symbol == sym, EstimateSnapshot.eps_avg.isnot(None))
            .order_by(EstimateSnapshot.ts.desc())
            .limit(6)
        ).all()
        if len(rows) < 2:
            continue
        latest_ts, latest = rows[0]
        prior = next((e for t, e in rows[1:] if (latest_ts - t).days >= 7), None)
        if prior:
            out[sym] = (float(latest) - float(prior)) / abs(float(prior))
    return out


def build_alignment_screen(
    session: Session, settings: Settings | None = None, *, days: int = 7, top: int = 25
) -> list[dict[str, Any]]:
    """This week's reporters ranked by angle/flow/EPS-revision alignment."""
    settings = settings or get_settings()
    reporters = _reporters(session, days)
    angles = _angles(session)
    flows = _flows(session)
    revs = _revisions(session, list(reporters))

    rows: list[dict[str, Any]] = []
    for sym, ev in reporters.items():
        angle = angles.get(sym)
        if angle is None or angle.get("angle") is None:
            continue  # require a research angle (Fahad's 'hidden angle' gate)
        fl = flows.get(sym)
        flow_val = fl.get("net_dollar_delta") if fl else None
        rev = revs.get(sym)
        res = score_alignment(
            AlignmentInputs(
                angle=angle["angle"], flow=flow_val, revision=rev,
                confidence=angle.get("confidence"),
            )
        )
        rows.append(
            {
                "symbol": sym,
                "date": ev.get("date"),
                "session": ev.get("session"),
                "angle": angle["angle"],
                "themes": (angle.get("themes") or [])[:3],
                "flow": flow_val,
                "flow_label": fl.get("label") if fl else None,
                "revision": rev,
                "tier": res.tier,
                "tier_rank": res.tier_rank,
                "score": res.score,
                "bias": res.bias,
                "aligned": res.aligned,
            }
        )
    rows.sort(key=lambda r: (r["tier_rank"], -r["score"]))
    return rows[:top]
