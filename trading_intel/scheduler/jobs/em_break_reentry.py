"""Scheduled job (daily): post-earnings re-entry scan -> signals.

Assembles the descriptor features for each watchlist name from the banked tables
(EM-break, gamma burn-off, walls, dealer lean, index systematic tailwind) and
hands them to ``strategies.em_break_reentry.emit_signals`` — the only layer
allowed to write to ``signals`` (CLAUDE.md). Rows are ``experimental=True`` until
the P6 backtest validates them.

Enrichment TODOs (documented in the plan): ``straddle_label`` (get_straddle
decay), ``vrp_normalizing`` (get_vol_richness) and per-name ``overwriter_rebuilding``
(ΔOI+ΔIV pairing) are left ``None`` here; the gate degrades gracefully without
them. Descriptor composite, not a raw-Greek alert (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.em_break_reentry
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import GexRolling, OiChainEod
from trading_intel.mcp.em_tools import get_em_break, get_gamma_burnoff, get_systematic_flow
from trading_intel.strategies.em_break_reentry import emit_signals
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)


def _walls(session: Session, sym: str, *, dte_max: int = 60) -> tuple[float | None, float | None]:
    """Call/put wall (strike with max side gxoi) from the latest oi_chain snapshot."""
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == sym)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None, None
    rows = session.execute(
        select(OiChainEod.strike, OiChainEod.cp, func.sum(OiChainEod.gxoi).label("g"))
        .where(
            OiChainEod.symbol == sym,
            OiChainEod.ts == ts,
            OiChainEod.dte >= 0,
            OiChainEod.dte <= dte_max,
        )
        .group_by(OiChainEod.strike, OiChainEod.cp)
    ).all()
    calls = [(r.strike, r.g) for r in rows if str(r.cp).upper().startswith("C") and r.g]
    puts = [(r.strike, r.g) for r in rows if str(r.cp).upper().startswith("P") and r.g]
    call_wall = max(calls, key=lambda t: t[1])[0] if calls else None
    put_wall = max(puts, key=lambda t: t[1])[0] if puts else None
    return (
        float(call_wall) if call_wall is not None else None,
        float(put_wall) if put_wall is not None else None,
    )


def _dealer_sign(session: Session, sym: str) -> float | None:
    """Sign of latest net GEX (gex_total): +1 long gamma (damping), -1 short."""
    total = session.execute(
        select(GexRolling.gex_total)
        .where(GexRolling.symbol == sym)
        .order_by(GexRolling.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if total is None:
        return None
    return 1.0 if total > 0 else -1.0 if total < 0 else 0.0


def build_features(session: Session, settings: Settings) -> dict[str, dict]:
    """Feature dict per watchlist name that has both an EM-break and burn-off read."""
    sysflow = get_systematic_flow(session)
    tailwind = sysflow.get("total_buying_usd") if sysflow.get("found") else None

    out: dict[str, dict] = {}
    for sym in settings.watchlist_symbols:
        emb = get_em_break(session, sym)
        gb = get_gamma_burnoff(session, sym)
        if not emb.get("found") or not gb.get("found"):
            continue
        brk = emb.get("em_break", {})
        ov = emb.get("over_realization", {})
        call_wall, put_wall = _walls(session, sym)
        out[sym] = {
            "em_broke": brk.get("broke"),
            "over_realizing": ov.get("persisting"),
            "break_ratio": brk.get("break_ratio"),
            "sigma": brk.get("sigma"),
            "direction": brk.get("direction"),
            "gamma_burned_off": gb.get("burned_off"),
            "phase": gb.get("phase"),
            "spot": gb.get("spot"),
            "put_wall": put_wall,
            "call_wall": call_wall,
            "dealer_gamma_sign": _dealer_sign(session, sym),
            "systematic_buying_usd": tailwind,
            # Enrichment TODOs (see module docstring): left None -> no contribution.
            "straddle_label": None,
            "vrp_normalizing": None,
            "overwriter_rebuilding": None,
        }
    return out


def run(session: Session, *, settings: Settings | None = None) -> None:
    """Build features and emit experimental re-entry signals."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="em_break_reentry")
    as_of = eastern_now().date()
    features = build_features(session, settings)
    emitted = emit_signals(session, features, as_of=as_of)
    session.commit()
    bound.info("em_break_reentry.done", candidates=len(features), emitted=len(emitted))


def main() -> None:
    """Manual entrypoint: wire Settings -> session, run once."""
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings=settings)


if __name__ == "__main__":
    main()
