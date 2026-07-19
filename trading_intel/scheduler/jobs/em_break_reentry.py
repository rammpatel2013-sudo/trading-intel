"""Scheduled job (daily): post-earnings re-entry scan -> signals.

Assembles the descriptor features for each watchlist name from the banked tables
(EM-break, gamma burn-off, walls, dealer lean, index systematic tailwind) and
hands them to ``strategies.em_break_reentry.emit_signals`` - the only layer
allowed to write to ``signals`` (CLAUDE.md). Rows are ``experimental=True`` until
the P6 backtest validates them.

Enrichment hooks now wired: ``straddle_label`` (get_straddle day-over-day decay),
``vrp_normalizing`` (get_vol_richness percentile/label) and per-name
``overwriter_rebuilding`` (above-spot call OI/IV change pairing via oi_chain_eod ->
overwriter_call_supply). Each is a soft bonus in the gate and degrades to ``None``
gracefully. Descriptor composite, not a raw-Greek alert (FlashAlpha rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.em_break_reentry
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.flows import CallStrikeChange, overwriter_call_supply
from trading_intel.mcp.em_tools import get_em_break, get_gamma_burnoff, get_systematic_flow
from trading_intel.mcp.extra_tools import get_straddle, get_vol_richness
from trading_intel.memory.models import GexRolling, OiChainEod
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


def _straddle_label(straddle_res: Mapping) -> str | None:
    """'decaying' / 'repricing_up' / 'flat' from get_straddle's day-over-day decay.

    None when the straddle can't be priced or there's no prior snapshot to diff.
    """
    if not straddle_res.get("found"):
        return None
    return (straddle_res.get("decay") or {}).get("label")


def _vrp_normalizing(vr_row: Mapping | None) -> bool | None:
    """Post-earnings event premium deflating.

    True when the variance risk premium is no longer in the top half of the name's
    own history (scale-robust percentile) or the rich/cheap label says not-rich -
    i.e. the earnings IV crush has handed vol back to realized. Single-snapshot
    PROXY; a true pre/post-print VRP delta is the later refinement. None if no read.
    """
    if not vr_row:
        return None
    p = vr_row.get("vrp_pctile")
    if p is not None:
        p = p / 100.0 if p > 1.0 else p  # accept 0-1 or 0-100 percentile scales
        return bool(p <= 0.5)
    lab = (vr_row.get("label") or "").strip().lower()
    if lab:
        return lab not in ("rich", "expensive", "very_rich")
    return None


def _overwriter_rebuilding(changes: Sequence[CallStrikeChange]) -> bool | None:
    """True when above-spot call OI is opening with IV softening - supply-led writing
    rebuilding the call wall (the oi-flow-direction rule). None if no paired strikes.
    """
    if not changes:
        return None
    return bool(overwriter_call_supply(changes)["supply_led"])


def _call_strike_changes(
    session: Session, sym: str, spot: float | None, *, dte_max: int = 60
) -> list[CallStrikeChange]:
    """Above-spot call strikes' OI change + IV change (latest vs prior EOD oi_chain_eod ts).

    Aggregated across expiries per strike (OI-weighted IV). The IV change is required
    by the oi-flow-direction rule to separate writing (supply) from buying (demand),
    so we read the chain directly rather than ``get_oi_changes`` (which omits IV).
    """
    if spot is None or spot <= 0:
        return []
    ts_rows = (
        session.execute(
            select(OiChainEod.ts)
            .where(OiChainEod.symbol == sym)
            .distinct()
            .order_by(OiChainEod.ts.desc())
            .limit(2)
        )
        .scalars()
        .all()
    )
    if len(ts_rows) < 2:
        return []
    ts_now, ts_prev = ts_rows[0], ts_rows[1]

    def _by_strike(ts: datetime) -> dict[float, tuple[float, float, float]]:
        rows = session.execute(
            select(OiChainEod.strike, OiChainEod.oi, OiChainEod.iv, OiChainEod.gxoi).where(
                OiChainEod.symbol == sym,
                OiChainEod.ts == ts,
                OiChainEod.cp == "C",
                OiChainEod.dte >= 0,
                OiChainEod.dte <= dte_max,
                OiChainEod.strike > spot,
            )
        ).all()
        agg: dict[float, list[float]] = {}
        for r in rows:
            if r.strike is None:
                continue
            oi = float(r.oi or 0.0)
            a = agg.setdefault(float(r.strike), [0.0, 0.0, 0.0, 0.0])  # oi, gxoi, iv*w sum, w sum
            a[0] += oi
            a[1] += float(r.gxoi or 0.0)
            if r.iv is not None:
                w = max(oi, 1.0)
                a[2] += float(r.iv) * w
                a[3] += w
        return {k: (v[0], (v[2] / v[3] if v[3] else float("nan")), v[1]) for k, v in agg.items()}

    now = _by_strike(ts_now)
    prev = _by_strike(ts_prev)
    out: list[CallStrikeChange] = []
    for strike, (oi_n, iv_n, gx_n) in now.items():
        if strike not in prev:
            continue
        oi_p, iv_p, _ = prev[strike]
        if iv_n != iv_n or iv_p != iv_p:  # NaN guard - missing IV either side
            continue
        out.append(CallStrikeChange(strike=strike, d_oi=oi_n - oi_p, d_iv=iv_n - iv_p, gxoi=gx_n))
    return out


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
        spot_val = gb.get("spot")
        call_wall, put_wall = _walls(session, sym)

        # Enrichment hooks (soft bonuses in the gate; each degrades to None cleanly).
        straddle_res = get_straddle(session, sym)
        vr = get_vol_richness(session, [sym], settings=settings)
        vr_row = next(iter(vr.get("rows", [])), None) if vr.get("found") else None
        changes = _call_strike_changes(session, sym, spot_val)

        out[sym] = {
            "em_broke": brk.get("broke"),
            "over_realizing": ov.get("persisting"),
            "break_ratio": brk.get("break_ratio"),
            "sigma": brk.get("sigma"),
            "direction": brk.get("direction"),
            "gamma_burned_off": gb.get("burned_off"),
            "phase": gb.get("phase"),
            "spot": spot_val,
            "put_wall": put_wall,
            "call_wall": call_wall,
            "dealer_gamma_sign": _dealer_sign(session, sym),
            "systematic_buying_usd": tailwind,
            "straddle_label": _straddle_label(straddle_res),
            "vrp_normalizing": _vrp_normalizing(vr_row),
            "overwriter_rebuilding": _overwriter_rebuilding(changes),
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
