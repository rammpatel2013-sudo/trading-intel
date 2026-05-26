"""Scheduled job (EOD): the volatility-richness scan.

Ranks each watchlist name's IV richness vs a *forward* RV forecast and writes one
``vol_richness`` row per (symbol, trading-day, horizon). Reads STORED data only —
no Convex / vendor call (so it is rule-1 safe and needs no ``OptionsDataSource``):

- ATM IV @30 / @60  — from the latest ``oi_chain_eod`` chain, via the delta
  surface, interpolated in total-variance space (``vol.richness``).
- forward RV @30 / @60 — HAR-RV (EWMA fallback) off the ``quotes_daily`` close
  series (``prices.forecast_vol``).
- standardization — the name's own trailing ``vol_richness`` history
  (``vrp_pctile`` / ``iv_rank``); cold until enough history accrues.
- regime gate — the latest ``vix_data`` level drives the mandatory VEGA/VIX
  tail-risk overlay (``vol.term_skew``): rich/short-vol reads are gated OFF in
  stress (> 32).

Idempotent: ``INSERT ... ON CONFLICT (symbol, ts, horizon_dte) DO UPDATE`` with
``ts`` the trading day, so a same-day re-run refreshes rather than duplicates
(CLAUDE.md rule 5). Descriptor only — emits no signals (FlashAlpha rule 4); the
promotion to a ``strategies/`` SignalGenerator happens only after the backtest.

Manual run:
    python -m trading_intel.scheduler.jobs.vol_richness
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
from trading_intel.memory.models import OiChainEod, QuoteDaily, VixData, VolRichness
from trading_intel.prices.forecast_vol import forecast_vol
from trading_intel.timeutils import eastern_now
from trading_intel.vol.richness import (
    RichnessInputs,
    atm_iv_at_horizon,
    build_richness_row,
)
from trading_intel.vol.term_skew import build_regime_gate, gated_label, term_slope
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

#: Locked horizons (calendar days): 30d headline, 60d (~VIX3M) confirmation.
HORIZONS: tuple[int, ...] = (30, 60)
_UQ_COLS = ["symbol", "ts", "horizon_dte"]
_UPDATE_COLS = (
    "iv_atm", "fcst_rv", "vrp_pts", "vrp_pctile", "iv_rank",
    "term_slope", "skew_25d", "regime_zone", "richness_score", "label",
)


# ── Stored-data readers ────────────────────────────────────────────────


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
            OiChainEod.symbol == symbol, OiChainEod.ts == ts, OiChainEod.iv.is_not(None)
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


def _close_series(session: Session, symbol: str) -> pd.Series:
    """Daily close series for ``symbol`` (oldest first). Empty if none stored."""
    rows = session.execute(
        select(QuoteDaily.close)
        .where(QuoteDaily.symbol == symbol, QuoteDaily.close.is_not(None))
        .order_by(QuoteDaily.date.asc())
    ).all()
    return pd.Series([float(r[0]) for r in rows], dtype=float)


def _history(
    session: Session, symbol: str, horizon: int, *, before: date
) -> tuple[list[float], list[float]]:
    """Trailing (iv_atm, vrp_pts) for the name/horizon, prior to ``before``.

    Used to standardize today's read against the name's own past — read before
    the upsert so today's row never contaminates its own percentile.
    """
    rows = session.execute(
        select(VolRichness.iv_atm, VolRichness.vrp_pts)
        .where(
            VolRichness.symbol == symbol,
            VolRichness.horizon_dte == horizon,
            VolRichness.ts < before,
        )
        .order_by(VolRichness.ts.asc())
    ).all()
    iv_hist = [float(r[0]) for r in rows if r[0] is not None]
    vrp_hist = [float(r[1]) for r in rows if r[1] is not None]
    return iv_hist, vrp_hist


def _latest_vix(session: Session) -> float | None:
    """Most recent stored VIX level (for the regime gate)."""
    val = session.execute(
        select(VixData.vix)
        .where(VixData.vix.is_not(None))
        .order_by(VixData.date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return float(val) if val is not None else None


def _skew_at_horizon(surface: DeltaSurface, horizon: int) -> float | None:
    """25Δ put skew (decimal, ``iv_put25 - iv_call25``) at the expiry nearest ``horizon``.

    Computed directly off the delta surface (same definition as
    ``surface_report.surface_metrics`` uses) so the EOD job stays decoupled from
    the synthesis/report layer.
    """
    if surface.n_expiries == 0:
        return None
    i25 = int(np.argmin(np.abs(surface.deltas - 25.0)))
    j = int(np.argmin(np.abs(surface.dte - horizon)))
    skew = float(surface.iv_put[j, i25] - surface.iv_call[j, i25])
    return skew if np.isfinite(skew) else None


# ── Row assembly (pure given the session reads) ────────────────────────


def build_rows(
    session: Session,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Compute the day's ``vol_richness`` rows from stored data (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or effective_symbols(session, settings)
    gate = build_regime_gate(_latest_vix(session))

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

        iv_by_h = {
            h: atm_iv_at_horizon(surface.dte, surface.atm_iv, h) for h in HORIZONS
        }
        # Single per-name calendar slope (iv_60 - iv_30), shared across rows.
        slope = term_slope(iv_by_h.get(30), iv_by_h.get(60))

        forecasts = forecast_vol(_close_series(session, symbol), horizons=HORIZONS)

        for h in HORIZONS:
            iv_atm = iv_by_h.get(h)
            if iv_atm is None:
                continue
            vf = forecasts.get(h)
            fcst = None
            if vf is not None:
                fcst = vf.har_rv if vf.har_rv is not None else vf.ewma_rv
            if fcst is None:
                continue  # no forward-RV forecast → no VRP to record

            iv_hist, vrp_hist = _history(session, symbol, h, before=as_of)
            row = build_richness_row(
                RichnessInputs(
                    symbol=symbol,
                    horizon_dte=h,
                    iv_atm=iv_atm,
                    forecast_rv=fcst,
                    iv_history=iv_hist,
                    vrp_history=vrp_hist,
                )
            )
            records.append(
                {
                    "symbol": row.symbol,
                    "ts": as_of,
                    "horizon_dte": h,
                    "iv_atm": row.iv_atm,
                    "fcst_rv": row.forecast_rv,
                    "vrp_pts": row.vrp_pts,
                    "vrp_pctile": row.vrp_pctile,
                    "iv_rank": row.iv_rank,
                    "term_slope": slope,
                    "skew_25d": _skew_at_horizon(surface, h),
                    "regime_zone": gate.zone,
                    "richness_score": row.richness_score,
                    "label": gated_label(row.label, gate),
                }
            )
    return records


def _upsert(session: Session, records: list[dict]) -> None:
    """Idempotent upsert into ``vol_richness`` (refresh on the natural key)."""
    if not records:
        return
    stmt = pg_insert(VolRichness).values(records)
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
    """Build today's vol-richness rows and upsert them into ``vol_richness``."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="vol_richness")

    as_of = eastern_now().date()
    records = build_rows(session, settings, as_of=as_of, symbols=symbols)
    _upsert(session, records)
    session.commit()

    n_symbols = len({r["symbol"] for r in records})
    bound.info(
        "vol_richness.done", as_of=as_of.isoformat(), rows=len(records), symbols=n_symbols
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
