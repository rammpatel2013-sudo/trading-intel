"""Scheduled job (EOD): index-level skew snapshot -> ``index_skew_daily``.

Composes one row/day:
- Cboe SKEW (index third-moment estimator) — via ``CboeClient.skew_index()``.
- Nations SDEX — via ``prices.fetch_sdex()`` (Yahoo ``^SDEX``). The Cboe CDN
  doesn't expose the Nations symbol; Yahoo is the source of truth. ADR-003 §7
  open question 1 (SPY-proxy fallback) remains documented but unused.
- SPX 25Δ RR @ 30d — computed off the latest ``oi_chain_eod`` SPX surface via
  ``vol.skew.risk_reversal``; trailing 252d percentile from prior rows.
- VVIX — mirrored from ``vix_data`` for self-contained queries.
- VIX call-wing skew + OTM-call OI share — aggregated from today's
  ``vix_options_chain`` snapshot via ``vol.vix_skew``.
- ``vix_tail_hedging_score`` — z-summed composite of the three independent
  proxies above (call skew, OI share, VVIX/VIX). The 252d z-scoring is done
  here against trailing ``index_skew_daily`` rows.

Idempotent: row keyed on ``date`` PK with ``ON CONFLICT DO UPDATE``.

Manual run:
    python -m trading_intel.scheduler.jobs.index_skew
"""

from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from trading_intel.clients.cboe import CboeClient
from trading_intel.clients.prices import (
    fetch_cor1m,
    fetch_cor3m,
    fetch_dspx,
    fetch_sdex,
    fetch_tdex,
    fetch_vixeq,
    fetch_voli,
)
from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_delta_surface
from trading_intel.memory.models import (
    IndexSkewDaily,
    OiChainEod,
    QuoteDaily,
    VixData,
    VixOptionsChain,
)
from trading_intel.timeutils import eastern_now
from trading_intel.vol.nations_dex import compute_dex_triplet
from trading_intel.vol.skew import risk_reversal, skew_percentile
from trading_intel.vol.vix_regime import compute_decomposition
from trading_intel.vol.vix_skew import (
    vix_call_oi_share,
    vix_call_skew,
    vix_tail_hedging_score,
)

log = structlog.get_logger(__name__)

#: Index proxy used for the per-day SPX 25Δ RR. Falls back if missing.
SPX_PROXY_SYMBOL = "SPX"
SPY_PROXY_SYMBOL = "SPY"

_UPDATE_COLS = (
    "cboe_skew",
    "sdex",
    "spx_rr_25d_30d",
    "spx_rr_pctile_252d",
    "sdex_pctile_252d",
    "vvix",
    "vix_call_skew_25d",
    "vix_call_oi_share",
    "vix_tail_hedging_score",
    # Nations family (migration 0022).
    "voli",
    "voli_pctile_252d",
    "tdex",
    "tdex_pctile_252d",
    "calldex_proxy",
    "calldex_proxy_pctile_252d",
    "putdex_proxy",
    "putdex_proxy_pctile_252d",
    "riskdex_proxy",
    "riskdex_proxy_pctile_252d",
    # VIX decomposition family (migration 0023).
    "vix9d",
    "vix3m",
    "vix6m",
    "vix_voli_spread",
    "vix_term_9d_30d",
    "vix_term_3m_30d",
    "vix_spx_beta_60d",
    "vvix_vix_ratio",
    "vix_options_richness",
    # Cboe implied-correlation / dispersion family (migration 0025).
    "cor1m",
    "cor1m_pctile_252d",
    "cor3m",
    "cor3m_pctile_252d",
    # Cboe constituent-vol / dispersion family (migration 0027).
    "vixeq",
    "vixeq_pctile_252d",
    "dspx",
    "dspx_pctile_252d",
    "vixeq_vix_spread",
)


# ── Stored-data readers ────────────────────────────────────────────────


def _latest_chain_for(session: Session, symbol: str) -> pd.DataFrame | None:
    ts = session.execute(
        select(OiChainEod.ts)
        .where(OiChainEod.symbol == symbol)
        .order_by(OiChainEod.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if ts is None:
        return None
    rows = session.execute(
        select(OiChainEod.cp, OiChainEod.iv, OiChainEod.delta, OiChainEod.expiry).where(
            OiChainEod.symbol == symbol,
            OiChainEod.ts == ts,
            OiChainEod.iv.is_not(None),
        )
    ).all()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["cp", "iv", "delta", "expiry"]).dropna(
        subset=["iv", "expiry"]
    )
    if df.empty:
        return None
    df["opt_kind"] = df["cp"]
    df["expiration"] = pd.to_datetime(df["expiry"])
    return df


def _todays_vix_chain(session: Session, *, as_of: date) -> pd.DataFrame:
    rows = session.execute(
        select(
            VixOptionsChain.expiration,
            VixOptionsChain.strike,
            VixOptionsChain.opt_kind,
            VixOptionsChain.delta,
            VixOptionsChain.iv,
            VixOptionsChain.oi,
            VixOptionsChain.volume,
        ).where(VixOptionsChain.ts == as_of)
    ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(
        rows,
        columns=["expiration", "strike", "opt_kind", "delta", "iv", "oi", "volume"],
    )


def _latest_vix_vvix(session: Session, *, as_of: date) -> tuple[float | None, float | None]:
    row = session.execute(
        select(VixData.vix, VixData.vvix)
        .where(VixData.date <= as_of)
        .order_by(VixData.date.desc())
        .limit(1)
    ).first()
    if row is None:
        return (None, None)
    vix = float(row[0]) if row[0] is not None else None
    vvix = float(row[1]) if row[1] is not None else None
    return (vix, vvix)


def _spx_vix_closes(
    session: Session, *, as_of: date, lookback_days: int = 90
) -> tuple[pd.Series, pd.Series]:
    """Date-indexed SPX + VIX close series for the β regression."""
    spx_rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.close)
        .where(QuoteDaily.symbol == "SPX", QuoteDaily.date <= as_of)
        .order_by(QuoteDaily.date.desc())
        .limit(lookback_days)
    ).all()
    vix_rows = session.execute(
        select(VixData.date, VixData.vix)
        .where(VixData.vix.is_not(None), VixData.date <= as_of)
        .order_by(VixData.date.desc())
        .limit(lookback_days)
    ).all()
    spx = (
        pd.Series(
            [float(r[1]) for r in spx_rows],
            index=pd.to_datetime([r[0] for r in spx_rows]),
        ).sort_index()
        if spx_rows
        else pd.Series(dtype=float)
    )
    vix = (
        pd.Series(
            [float(r[1]) for r in vix_rows],
            index=pd.to_datetime([r[0] for r in vix_rows]),
        ).sort_index()
        if vix_rows
        else pd.Series(dtype=float)
    )
    return spx, vix


def _history_series(
    session: Session,
    column: ColumnElement[float],
    *,
    before: date,
    limit: int = 300,
) -> list[float]:
    """Trailing values for one column on ``index_skew_daily`` (oldest first)."""
    rows = session.execute(
        select(IndexSkewDaily.date, column)
        .where(column.is_not(None), IndexSkewDaily.date < before)
        .order_by(IndexSkewDaily.date.desc())
        .limit(limit)
    ).all()
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r[0])
    return [float(r[1]) for r in rows]


def _z(history: list[float], current: float | None) -> float | None:
    """Z-score of ``current`` against the trailing distribution."""
    if current is None or not np.isfinite(current):
        return None
    if len(history) < 20:
        return None
    arr = np.asarray(history, dtype=float)
    sd = float(arr.std(ddof=1))
    if not np.isfinite(sd) or sd <= 0:
        return None
    return (current - float(arr.mean())) / sd


# ── Row assembly ───────────────────────────────────────────────────────


def _spx_delta_surface(session: Session, *, as_of: date):
    """Build and return the first usable SPX/SPY delta surface, or ``None``.

    Shared between the 25Δ RR read and the Nations CallDex/PutDex/RiskDex
    proxies (``vol.nations_dex``) so we only build the surface once per row.
    """
    for sym in (SPX_PROXY_SYMBOL, SPY_PROXY_SYMBOL):
        chain = _latest_chain_for(session, sym)
        if chain is None:
            continue
        try:
            return build_delta_surface(chain, ref=as_of)
        except ComputationError:
            continue
    return None


def _spx_rr_30d(session: Session, *, as_of: date) -> float | None:
    """SPX (or SPY fallback) 25Δ RR @ 30d."""
    surface = _spx_delta_surface(session, as_of=as_of)
    if surface is None:
        return None
    return risk_reversal(surface, delta=25.0, horizon_dte=30)


def build_row(
    session: Session,
    cboe: CboeClient,
    *,
    as_of: date | None = None,
) -> dict | None:
    """Compose one ``index_skew_daily`` row from stored + CBOE data."""
    as_of = as_of or eastern_now().date()
    cboe_skew = cboe.skew_index()
    # Nations indices via Yahoo. SDEX/TDEX/VOLI are public; CallDex/PutDex/
    # RiskDex are subscription-only and computed as proxies below.
    sdex = fetch_sdex()
    voli = fetch_voli()
    tdex = fetch_tdex()
    # Cboe implied correlation (dispersion) via Yahoo — same degrade-to-None path.
    cor1m = fetch_cor1m()
    cor3m = fetch_cor3m()
    # Cboe constituent-vol (^VIXEQ) + dispersion index (^DSPX), same Yahoo path.
    vixeq = fetch_vixeq()
    dspx = fetch_dspx()

    # VIX term-structure tenors from Cboe — drives the TERM dimension of the
    # vol-regime decomposition. Each tenor degrades to None on Cboe outage.
    term = cboe.term_structure()
    vix9d_val = term.get("VIX9D")
    vix3m_val = term.get("VIX3M")
    vix6m_val = term.get("VIX6M")

    # Build the SPX delta surface once; the 25Δ RR and Nations CallDex/PutDex
    # /RiskDex proxies all read off it.
    surface = _spx_delta_surface(session, as_of=as_of)
    spx_rr = (
        risk_reversal(surface, delta=25.0, horizon_dte=30) if surface is not None else None
    )
    calldex_p, putdex_p, riskdex_p = (
        compute_dex_triplet(surface) if surface is not None else (None, None, None)
    )

    spx_rr_hist = _history_series(session, IndexSkewDaily.spx_rr_25d_30d, before=as_of, limit=300)
    spx_rr_pctile = (
        skew_percentile(spx_rr_hist[-252:], spx_rr) if spx_rr is not None else None
    )

    sdex_hist = _history_series(session, IndexSkewDaily.sdex, before=as_of, limit=300)
    sdex_pctile = skew_percentile(sdex_hist[-252:], sdex) if sdex is not None else None

    voli_hist = _history_series(session, IndexSkewDaily.voli, before=as_of, limit=300)
    voli_pctile = skew_percentile(voli_hist[-252:], voli) if voli is not None else None

    tdex_hist = _history_series(session, IndexSkewDaily.tdex, before=as_of, limit=300)
    tdex_pctile = skew_percentile(tdex_hist[-252:], tdex) if tdex is not None else None

    cor1m_hist = _history_series(session, IndexSkewDaily.cor1m, before=as_of, limit=300)
    cor1m_pctile = skew_percentile(cor1m_hist[-252:], cor1m) if cor1m is not None else None

    cor3m_hist = _history_series(session, IndexSkewDaily.cor3m, before=as_of, limit=300)
    cor3m_pctile = skew_percentile(cor3m_hist[-252:], cor3m) if cor3m is not None else None

    vixeq_hist = _history_series(session, IndexSkewDaily.vixeq, before=as_of, limit=300)
    vixeq_pctile = skew_percentile(vixeq_hist[-252:], vixeq) if vixeq is not None else None

    dspx_hist = _history_series(session, IndexSkewDaily.dspx, before=as_of, limit=300)
    dspx_pctile = skew_percentile(dspx_hist[-252:], dspx) if dspx is not None else None

    calldex_hist = _history_series(
        session, IndexSkewDaily.calldex_proxy, before=as_of, limit=300
    )
    calldex_pctile = (
        skew_percentile(calldex_hist[-252:], calldex_p) if calldex_p is not None else None
    )

    putdex_hist = _history_series(
        session, IndexSkewDaily.putdex_proxy, before=as_of, limit=300
    )
    putdex_pctile = (
        skew_percentile(putdex_hist[-252:], putdex_p) if putdex_p is not None else None
    )

    riskdex_hist = _history_series(
        session, IndexSkewDaily.riskdex_proxy, before=as_of, limit=300
    )
    riskdex_pctile = (
        skew_percentile(riskdex_hist[-252:], riskdex_p) if riskdex_p is not None else None
    )

    vix_chain = _todays_vix_chain(session, as_of=as_of)
    vix_skew = vix_call_skew(vix_chain, abs_delta=0.25)
    vix_oi_share = vix_call_oi_share(vix_chain, otm_delta_cutoff=0.30)

    _vix, vvix = _latest_vix_vvix(session, as_of=as_of)
    # We z-score VVIX directly; VIX is in the denominator of the VVIX/VIX
    # ratio but the regime composite is dominated by VVIX moves so a single
    # standardization suffices and avoids a noisy second join (see ADR-003 §3.4).

    # VIX decomposition (migration 0023): use today's VIX + VOLI + VVIX +
    # term-structure tenors, plus the SPX/VIX close series for the β regression.
    spx_closes, vix_closes = _spx_vix_closes(session, as_of=as_of, lookback_days=90)
    decomposition = compute_decomposition(
        vix=_vix,
        voli=voli,
        vvix=vvix,
        vix9d=vix9d_val,
        vix3m=vix3m_val,
        spx_closes=spx_closes,
        vix_closes=vix_closes,
    )

    # Z-score each component against trailing history.
    vix_skew_hist = _history_series(
        session, IndexSkewDaily.vix_call_skew_25d, before=as_of, limit=300
    )
    oi_share_hist = _history_series(
        session, IndexSkewDaily.vix_call_oi_share, before=as_of, limit=300
    )
    # VVIX/VIX history is reconstructed from vix_data (no ratio column there);
    # we approximate by z-scoring vvix only — directionally adequate for the
    # composite, since vix is in the denominator and dominated by vvix moves.
    vvix_hist_rows = session.execute(
        select(VixData.vvix)
        .where(VixData.vvix.is_not(None), VixData.date < as_of)
        .order_by(VixData.date.asc())
        .limit(300)
    ).all()
    vvix_hist = [float(r[0]) for r in vvix_hist_rows]
    score = vix_tail_hedging_score(
        call_skew_z=_z(vix_skew_hist, vix_skew),
        oi_share_z=_z(oi_share_hist, vix_oi_share),
        vvix_vix_z=_z(vvix_hist, vvix),
    )

    return {
        "date": as_of,
        "cboe_skew": cboe_skew,
        "sdex": sdex,
        "spx_rr_25d_30d": spx_rr,
        "spx_rr_pctile_252d": spx_rr_pctile,
        "sdex_pctile_252d": sdex_pctile,
        "vvix": vvix,
        "vix_call_skew_25d": vix_skew,
        "vix_call_oi_share": vix_oi_share,
        "vix_tail_hedging_score": score,
        "voli": voli,
        "voli_pctile_252d": voli_pctile,
        "tdex": tdex,
        "tdex_pctile_252d": tdex_pctile,
        "calldex_proxy": calldex_p,
        "calldex_proxy_pctile_252d": calldex_pctile,
        "putdex_proxy": putdex_p,
        "putdex_proxy_pctile_252d": putdex_pctile,
        "riskdex_proxy": riskdex_p,
        "riskdex_proxy_pctile_252d": riskdex_pctile,
        # VIX decomposition family (migration 0023).
        "vix9d": vix9d_val,
        "vix3m": vix3m_val,
        "vix6m": vix6m_val,
        "vix_voli_spread": decomposition["vix_voli_spread"],
        "vix_term_9d_30d": decomposition["vix_term_9d_30d"],
        "vix_term_3m_30d": decomposition["vix_term_3m_30d"],
        "vix_spx_beta_60d": decomposition["vix_spx_beta_60d"],
        "vvix_vix_ratio": decomposition["vvix_vix_ratio"],
        "vix_options_richness": decomposition["vix_options_richness"],
        # Cboe implied-correlation / dispersion family (migration 0025).
        "cor1m": cor1m,
        "cor1m_pctile_252d": cor1m_pctile,
        "cor3m": cor3m,
        "cor3m_pctile_252d": cor3m_pctile,
        # Cboe constituent-vol / dispersion family (migration 0027).
        "vixeq": vixeq,
        "vixeq_pctile_252d": vixeq_pctile,
        "dspx": dspx,
        "dspx_pctile_252d": dspx_pctile,
        "vixeq_vix_spread": (vixeq - _vix) if (vixeq is not None and _vix is not None) else None,
    }


def _upsert(session: Session, record: dict) -> None:
    stmt = pg_insert(IndexSkewDaily).values([record])
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    cboe: CboeClient,
    *,
    settings: Settings | None = None,
) -> None:
    """Compose and upsert today's ``index_skew_daily`` row."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="index_skew")

    as_of = eastern_now().date()
    record = build_row(session, cboe, as_of=as_of)
    if record is None:
        bound.warning("index_skew.no_record")
        return
    _upsert(session, record)
    session.commit()
    bound.info(
        "index_skew.done",
        as_of=as_of.isoformat(),
        cboe_skew=record.get("cboe_skew"),
        sdex=record.get("sdex"),
        score=record.get("vix_tail_hedging_score"),
    )


def main() -> None:
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
    cboe = CboeClient()
    with session_factory() as session:
        run(session, cboe, settings=settings)


if __name__ == "__main__":
    main()
