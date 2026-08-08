"""Scheduled job (EOD): constant-maturity forward IV for index ETFs.

The index-ETF complement to ``skew_snapshots``. SPY / QQQ / SPX are deliberately
kept out of the per-strike persisters (``CHAIN_EXCLUDE_ROOTS``), so the delta-
surface pipeline that feeds ``skew_snapshots`` has no stored chain for them.
This job instead pulls a LIVE chain from the ``OptionsDataSource`` (one wide
``chain_long`` call per root), builds the delta surface in memory, and writes
only a small aggregate row per (symbol, day, tenor) — no per-strike rows are
persisted, so the exclusion's storage intent is preserved.

For each configured constant-maturity tenor (``IV_TENOR_DTE``, e.g. 30 / 90 days)
it interpolates IV in TOTAL-VARIANCE space across the listed expiries so the
historical line never sawtooths as expiries roll. Stored per side in the equity
sign convention: ``iv_put_*`` is the downside (-Δ) wing, ``iv_call_*`` the upside
(+Δ) wing; 50Δ ≡ ATM is ``iv_atm``. A 25Δ risk reversal is ``iv_put_25d -
iv_call_25d`` at read time.

Idempotent: ``INSERT … ON CONFLICT (symbol, ts, tenor_dte) DO UPDATE`` (CLAUDE.md
rule 5). Regime descriptor only (FlashAlpha rule 4) — emits no signals.

Manual run:
    python -m trading_intel.scheduler.jobs.iv_tenor_snapshots
"""

from __future__ import annotations

import uuid
from datetime import date

import numpy as np
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import ComputationError, TradingIntelError
from trading_intel.greeks.surface import build_delta_surface
from trading_intel.memory.models import IvTenorSnapshot, Ticker
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

#: Pull breadth. Index ETFs list near-daily expirations, so the *nearest* 40
#: expiries can all fall inside 90 DTE and fail to bracket the 3-month tenor.
#: Pull (and keep) enough expiries to reach well past the longest tenor under
#: either a daily (~80 expiries ≈ 112 calendar days) or weekly cadence. The
#: constant-maturity interp is piecewise-linear, so the extra long-dated nodes
#: never distort a nearer-tenor read — they only guarantee a bracketing pair.
_MAX_EXPS = 80
_MAX_EXPIRIES = 80  # keep every liquid expiry the pull returns for the interp
_STRIKE_RANGE = 0.30

_UQ_COLS = ["symbol", "ts", "tenor_dte"]
_UPDATE_COLS = (
    "iv_atm",
    "iv_call_15d",
    "iv_put_15d",
    "iv_call_25d",
    "iv_put_25d",
    "spot",
    "n_expiries",
)


# ── Constant-maturity interpolation ────────────────────────────────────


def cm_interp(dte: np.ndarray, vals: np.ndarray, target: float) -> float | None:
    """IV at ``target`` DTE via linear interpolation in total variance.

    ``var = iv**2 * dte/365`` is interpolated linearly against DTE and converted
    back to vol — the standard constant-maturity construction. Returns ``None``
    when fewer than two finite (dte, iv) points exist or ``target`` falls outside
    the observed DTE span (no extrapolation: a constant-maturity read needs a
    bracketing pair, otherwise the line would lie).
    """
    d = np.asarray(dte, dtype=float)
    v = np.asarray(vals, dtype=float)
    mask = np.isfinite(d) & np.isfinite(v) & (d > 0) & (v > 0)
    d, v = d[mask], v[mask]
    if d.size < 2:
        return None
    order = np.argsort(d)
    d, v = d[order], v[order]
    if target < d[0] or target > d[-1]:
        return None
    var = v**2 * d / 365.0
    var_t = float(np.interp(target, d, var))
    iv2 = var_t / (target / 365.0)
    return float(np.sqrt(iv2)) if iv2 > 0 else None


def _delta_col(deltas: np.ndarray, target: float) -> int:
    """Column index of the delta grid point nearest ``target`` (in percent)."""
    return int(np.argmin(np.abs(deltas - float(target))))


# ── Row assembly ───────────────────────────────────────────────────────


def build_rows(
    source: OptionsDataSource,
    settings: Settings,
    *,
    as_of: date | None = None,
    symbols: list[str] | None = None,
) -> list[dict]:
    """Compute the day's ``iv_tenor_snapshots`` rows from a live chain (no upsert)."""
    as_of = as_of or eastern_now().date()
    symbols = symbols or settings.iv_tenor_symbols
    tenors = settings.iv_tenor_dtes
    deltas = settings.iv_tenor_deltas
    # The two wing points we persist columns for (defaults 15/25); resolve to the
    # nearest configured delta so a custom IV_TENOR_DELTAS still maps cleanly.
    d15 = min(deltas, key=lambda x: abs(x - 15.0))
    d25 = min(deltas, key=lambda x: abs(x - 25.0))

    records: list[dict] = []
    for symbol in symbols:
        try:
            chain = source.chain_long(
                symbol, max_exps=_MAX_EXPS, strike_range=_STRIKE_RANGE
            )
        except TradingIntelError as exc:
            log.warning("iv_tenor.chain_failed", symbol=symbol, error=str(exc))
            continue
        if chain is None or chain.empty:
            log.warning("iv_tenor.chain_empty", symbol=symbol)
            continue

        try:
            surface = build_delta_surface(chain, n_expiries=_MAX_EXPIRIES, ref=as_of)
        except ComputationError as exc:
            log.warning("iv_tenor.surface_failed", symbol=symbol, error=str(exc))
            continue
        if surface.n_expiries < 2:
            continue  # need a bracketing pair for the constant-maturity interp

        spot: float | None
        try:
            spot = float(source.spot(symbol))
        except TradingIntelError:
            spot = None  # spot is diagnostic only — never fail the row for it

        c15 = _delta_col(surface.deltas, d15)
        c25 = _delta_col(surface.deltas, d25)
        atm = surface.atm_iv  # (T,)
        dte = surface.dte

        for tenor in tenors:
            iv_atm = cm_interp(dte, atm, tenor)
            iv_call_15d = cm_interp(dte, surface.iv_call[:, c15], tenor)
            iv_put_15d = cm_interp(dte, surface.iv_put[:, c15], tenor)
            iv_call_25d = cm_interp(dte, surface.iv_call[:, c25], tenor)
            iv_put_25d = cm_interp(dte, surface.iv_put[:, c25], tenor)
            if all(
                x is None
                for x in (iv_atm, iv_call_15d, iv_put_15d, iv_call_25d, iv_put_25d)
            ):
                continue  # nothing interpolated at this tenor — skip the row

            records.append(
                {
                    "symbol": symbol,
                    "ts": as_of,
                    "tenor_dte": int(tenor),
                    "iv_atm": iv_atm,
                    "iv_call_15d": iv_call_15d,
                    "iv_put_15d": iv_put_15d,
                    "iv_call_25d": iv_call_25d,
                    "iv_put_25d": iv_put_25d,
                    "spot": spot,
                    "n_expiries": int(surface.n_expiries),
                }
            )
    return records


def _ensure_tickers(session: Session, symbols: set[str]) -> None:
    """Idempotently seed ``tickers`` rows so the FK on the snapshot holds.

    Index ETFs may not be in the static watchlist; insert a bare row (symbol
    only) for any we are about to reference. ``ON CONFLICT DO NOTHING`` keeps it
    safe to re-run and never clobbers an existing, richer ticker row.
    """
    if not symbols:
        return
    stmt = pg_insert(Ticker).values([{"symbol": s} for s in sorted(symbols)])
    stmt = stmt.on_conflict_do_nothing(index_elements=["symbol"])
    session.execute(stmt)


def _upsert(session: Session, records: list[dict]) -> None:
    """Idempotent upsert into ``iv_tenor_snapshots`` (refresh on the natural key)."""
    if not records:
        return
    stmt = pg_insert(IvTenorSnapshot).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=_UQ_COLS,
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    as_of: date | None = None,
) -> None:
    """Build today's constant-maturity IV rows and upsert them."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="iv_tenor_snapshots")

    as_of = as_of or eastern_now().date()
    records = build_rows(source, settings, as_of=as_of, symbols=symbols)
    _ensure_tickers(session, {r["symbol"] for r in records})
    _upsert(session, records)
    session.commit()

    n_symbols = len({r["symbol"] for r in records})
    bound.info(
        "iv_tenor_snapshots.done",
        as_of=as_of.isoformat(),
        rows=len(records),
        symbols=n_symbols,
    )


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> ConvexClient, run once."""
    from trading_intel.clients.convex import ConvexClient
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
    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings)


if __name__ == "__main__":
    main()
