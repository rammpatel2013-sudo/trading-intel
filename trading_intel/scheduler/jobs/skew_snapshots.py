"""Scheduled job (EOD): per-name skew descriptors -> ``skew_snapshots``.

Mirrors the ``vol_richness`` job's shape: reads STORED data only (no vendor
call), builds a delta surface from the latest ``oi_chain_eod`` chain, computes
RR/BF at 10Δ and 25Δ across the configured horizons, standardizes against the
name's own trailing distribution (63d short / 252d long), regresses the name's
ATM IV against the VIX for the ``vix_beta_60d``, subtracts ``β x ΔSDEX`` from
the day's ΔRR for the ``rr_25d_abnormal`` residual, and labels the move shift-
vs-slide vs the prior-day row.

Idempotent: ``INSERT … ON CONFLICT (symbol, ts, horizon_dte) DO UPDATE`` (CLAUDE.md
rule 5). Per ADR-003 (revision 2), skew is signal-eligible — but this job is the
descriptor layer; signal emission happens in ``strategies/skew.py``.

Manual run:
    python -m trading_intel.scheduler.jobs.skew_snapshots
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

from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import DeltaSurface, build_delta_surface
from trading_intel.memory.models import (
    IndexSkewDaily,
    OiChainEod,
    SkewSnapshot,
    VixData,
)
from trading_intel.timeutils import eastern_now
from trading_intel.vol.skew import (
    butterfly,
    compose_label,
    front_back_slope,
    risk_reversal,
    shift_vs_slide,
    skew_percentile,
)
from trading_intel.vol.vix_beta import abnormal_rr_change, vix_beta
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

#: Locked horizons (calendar days) for the term-structure of skew.
HORIZONS: tuple[int, ...] = (30, 60, 90, 180, 365)

_UQ_COLS = ["symbol", "ts", "horizon_dte"]
_UPDATE_COLS = (
    "atm_iv",
    "rr_10d", "rr_25d", "bf_10d", "bf_25d",
    "rr_25d_pctile_63d", "rr_25d_pctile_252d", "bf_25d_pctile_252d",
    "front_back_rr_slope",
    "vix_beta_60d",
    "rr_25d_abnormal",
    "shift_slide_label",
    "label",
)


# ── Stored-data readers (mirrors vol_richness.py patterns) ─────────────


def _latest_chain(session: Session, symbol: str) -> pd.DataFrame | None:
    """Latest ``oi_chain_eod`` chain for ``symbol``, shaped for the delta surface."""
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


def _atm_iv_history(session: Session, symbol: str, horizon: int) -> pd.Series:
    """Trailing daily ATM-IV series for the name at one horizon (oldest first)."""
    rows = session.execute(
        select(SkewSnapshot.ts, SkewSnapshot.atm_iv)
        .where(
            SkewSnapshot.symbol == symbol,
            SkewSnapshot.horizon_dte == horizon,
            SkewSnapshot.atm_iv.is_not(None),
        )
        .order_by(SkewSnapshot.ts.asc())
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([float(r[1]) for r in rows], index=idx, dtype=float)


def _vix_close_history(session: Session) -> pd.Series:
    """Trailing daily VIX close series (oldest first) for VIX-beta regression."""
    rows = session.execute(
        select(VixData.date, VixData.vix)
        .where(VixData.vix.is_not(None))
        .order_by(VixData.date.asc())
    ).all()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.Series([float(r[1]) for r in rows], index=idx, dtype=float)


def _sdex_change(session: Session, *, as_of: date) -> float | None:
    """Day-over-day SDEX change in vol points; ``None`` if either day is missing."""
    rows = session.execute(
        select(IndexSkewDaily.date, IndexSkewDaily.sdex)
        .where(IndexSkewDaily.sdex.is_not(None), IndexSkewDaily.date <= as_of)
        .order_by(IndexSkewDaily.date.desc())
        .limit(2)
    ).all()
    if len(rows) < 2:
        return None
    today, prev = rows[0], rows[1]
    if today[0] != as_of:
        return None
    return float(today[1]) - float(prev[1])


def _rr_history(
    session: Session, symbol: str, horizon: int, *, before: date
) -> tuple[list[float], list[float]]:
    """Trailing 25Δ RR and 25Δ BF for (symbol, horizon) before ``before``.

    Used for the percentile standardization — read before the upsert so today's
    row never contaminates its own percentile.
    """
    rows = session.execute(
        select(SkewSnapshot.rr_25d, SkewSnapshot.bf_25d)
        .where(
            SkewSnapshot.symbol == symbol,
            SkewSnapshot.horizon_dte == horizon,
            SkewSnapshot.ts < before,
        )
        .order_by(SkewSnapshot.ts.asc())
    ).all()
    rr_hist = [float(r[0]) for r in rows if r[0] is not None]
    bf_hist = [float(r[1]) for r in rows if r[1] is not None]
    return rr_hist, bf_hist


def _prior_row(
    session: Session, symbol: str, horizon: int, *, before: date
) -> tuple[float | None, float | None]:
    """Most recent prior (ATM IV, 25Δ RR) for shift-vs-slide diffing."""
    row = session.execute(
        select(SkewSnapshot.atm_iv, SkewSnapshot.rr_25d)
        .where(
            SkewSnapshot.symbol == symbol,
            SkewSnapshot.horizon_dte == horizon,
            SkewSnapshot.ts < before,
        )
        .order_by(SkewSnapshot.ts.desc())
        .limit(1)
    ).first()
    if row is None:
        return (None, None)
    atm = float(row[0]) if row[0] is not None else None
    rr = float(row[1]) if row[1] is not None else None
    return (atm, rr)


# ── Row assembly ───────────────────────────────────────────────────────


def _surface_atm_at_horizon(surface: DeltaSurface, horizon: int) -> float | None:
    """Nearest-expiry ATM IV reading off ``DeltaSurface.atm_iv``."""
    if surface.n_expiries == 0:
        return None
    j = int(np.argmin(np.abs(surface.dte - horizon)))
    val = float(surface.atm_iv[j])
    return val if np.isfinite(val) else None


def build_rows(
    session: Session,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Compute the day's ``skew_snapshots`` rows from stored data (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or effective_symbols(session, settings)
    vix_series = _vix_close_history(session)
    d_sdex = _sdex_change(session, as_of=as_of)

    records: list[dict] = []
    for symbol in symbols:
        chain = _latest_chain(session, symbol)
        if chain is None:
            continue
        try:
            surface = build_delta_surface(chain, ref=as_of)
        except ComputationError:
            continue
        if surface.n_expiries == 0:
            continue

        # VIX-beta is per-name, horizon-agnostic for the 30d ATM-IV proxy.
        iv30_history = _atm_iv_history(session, symbol, 30)
        beta = vix_beta(iv30_history, vix_series)
        # Per-name slope across (30d, 180d) for the term-structure column.
        slope = front_back_slope(surface, delta=25.0, near_dte=30, far_dte=180)

        for h in HORIZONS:
            rr25 = risk_reversal(surface, delta=25.0, horizon_dte=h)
            rr10 = risk_reversal(surface, delta=10.0, horizon_dte=h)
            bf25 = butterfly(surface, delta=25.0, horizon_dte=h)
            bf10 = butterfly(surface, delta=10.0, horizon_dte=h)
            atm = _surface_atm_at_horizon(surface, h)
            if rr25 is None and bf25 is None and atm is None:
                continue  # nothing usable at this horizon — skip the row entirely

            rr_hist, bf_hist = _rr_history(session, symbol, h, before=as_of)
            pct_63 = skew_percentile(rr_hist[-63:], rr25) if rr25 is not None else None
            pct_252 = skew_percentile(rr_hist[-252:], rr25) if rr25 is not None else None
            bf_pct_252 = (
                skew_percentile(bf_hist[-252:], bf25) if bf25 is not None else None
            )

            prev_atm, prev_rr = _prior_row(session, symbol, h, before=as_of)
            d_atm_pts = (
                (atm - prev_atm) * 100.0 if (atm is not None and prev_atm is not None) else None
            )
            d_rr_pts = (
                (rr25 - prev_rr) * 100.0 if (rr25 is not None and prev_rr is not None) else None
            )
            slide_label = shift_vs_slide(d_atm_iv_pts=d_atm_pts, d_rr_pts=d_rr_pts)

            abnormal = abnormal_rr_change(
                d_rr_name=d_rr_pts, d_index_skew=d_sdex, beta=beta
            )

            records.append(
                {
                    "symbol": symbol,
                    "ts": as_of,
                    "horizon_dte": h,
                    "atm_iv": atm,
                    "rr_10d": rr10,
                    "rr_25d": rr25,
                    "bf_10d": bf10,
                    "bf_25d": bf25,
                    "rr_25d_pctile_63d": pct_63,
                    "rr_25d_pctile_252d": pct_252,
                    "bf_25d_pctile_252d": bf_pct_252,
                    "front_back_rr_slope": slope,
                    "vix_beta_60d": beta,
                    "rr_25d_abnormal": abnormal,
                    "shift_slide_label": slide_label,
                    "label": compose_label(
                        rr_pts=rr25 * 100.0 if rr25 is not None else None,
                        pctile_long=pct_252,
                    ),
                }
            )
    return records


def _upsert(session: Session, records: list[dict]) -> None:
    """Idempotent upsert into ``skew_snapshots`` (refresh on the natural key)."""
    if not records:
        return
    stmt = pg_insert(SkewSnapshot).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=_UQ_COLS,
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Build today's skew rows and upsert them into ``skew_snapshots``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="skew_snapshots")

    as_of = eastern_now().date()
    records = build_rows(session, settings, as_of=as_of, symbols=symbols)
    _upsert(session, records)
    session.commit()

    n_symbols = len({r["symbol"] for r in records})
    bound.info(
        "skew_snapshots.done", as_of=as_of.isoformat(), rows=len(records), symbols=n_symbols
    )


def main() -> None:
    """Manual entrypoint: wire Settings -> session, run once (no vendor client)."""
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
