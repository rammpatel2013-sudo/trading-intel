"""Scheduled job (EOD): daily per-name swing feature snapshots -> ``swing_features``.

Banks the Stage-1 feature vector (spot, ATM IV, RV20, IV/RV, RSI14, SMA50,
price-vs-SMA50, 25d skew, net GEX/DEX) plus the trailing-252d percentiles
(IV-rank, IV/RV, skew, GEX, DEX) so the percentile features mature into a real
distribution for the Stage-2 fitted model (see the swing-trade-system build).

Fed by CVForge (ADR-004) -- a LIVE chain/technicals pull, like ``greeks_snapshot``;
convexlib stays the primary regime engine (rule 1). Percentiles are standardized
against the name's own trailing 252d from this table (today excluded), matching
the ``skew_snapshots`` contract. Idempotent upsert on (symbol, ts) (rule 5).
Descriptive features only -- not signals (FlashAlpha rule 4).

Runs both on demand (``scripts/swing_features.py`` / ``run_swing_features.bat``)
and on the NAS (``python -m trading_intel.scheduler.jobs.swing_features`` from a
DSM task). NOTE: the extraction mirrors ``scripts/swing_report.py::analyze`` --
consolidate into ``trading_intel/swing`` at P3.

Manual run:
    python -m trading_intel.scheduler.jobs.swing_features
    python -m trading_intel.scheduler.jobs.swing_features AAPL NVDA
"""

from __future__ import annotations

import argparse
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import TypeVar

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import DataSourceError
from trading_intel.memory.models import SwingFeature
from trading_intel.timeutils import eastern_now
from trading_intel.vol.skew import skew_percentile

log = structlog.get_logger(__name__)

# Feature -> the column its trailing-252d percentile is written to.
_PCTILE_COL = {
    "atm_iv": "atm_iv_rank_252d",
    "iv_rv": "iv_rv_pctile_252d",
    "skew_25d": "skew_pctile_252d",
    "gex": "gex_pctile_252d",
    "dex": "dex_pctile_252d",
}
_RANKED = tuple(_PCTILE_COL)  # ranked features, in a stable order
_UPDATE_COLS = (
    "spot",
    "atm_iv",
    "rv20",
    "iv_rv",
    "rsi14",
    "sma50",
    "px_vs_sma50",
    "skew_25d",
    "gex",
    "dex",
    "atm_iv_rank_252d",
    "iv_rv_pctile_252d",
    "skew_pctile_252d",
    "gex_pctile_252d",
    "dex_pctile_252d",
)

_T = TypeVar("_T")


def _safe(fn: Callable[[], _T]) -> _T | None:
    """Run ``fn``; a transient CVForge ``DataSourceError`` (e.g. a 502) -> None."""
    try:
        return fn()
    except DataSourceError:
        return None


def realized_vol(closes: np.ndarray, window: int = 20) -> float | None:
    """Annualized close-to-close realized vol over the last ``window`` returns."""
    if closes.size < window + 1:
        return None
    rets = np.diff(np.log(closes[-(window + 1) :]))
    return float(rets.std(ddof=1) * np.sqrt(252))


def skew_25d(chain: pd.DataFrame, *, dte_lo: int = 25, dte_hi: int = 60) -> float | None:
    """25d put IV - 25d call IV on the nearest expiry in the DTE window (put skew > 0)."""
    if chain.empty or "delta" not in chain.columns:
        return None
    df = chain.dropna(subset=["delta", "iv", "expiration"]).copy()
    dte = (df["expiration"] - pd.Timestamp(date.today())).dt.days
    df = df[(dte >= dte_lo) & (dte <= dte_hi)]
    if df.empty:
        return None
    target = df.loc[(df["expiration"] - df["expiration"].min()).abs().idxmin(), "expiration"]
    df = df[df["expiration"] == target]
    calls = df[df["opt_kind"].str.upper().str[0] == "C"]
    puts = df[df["opt_kind"].str.upper().str[0] == "P"]
    if calls.empty or puts.empty:
        return None
    c = calls.iloc[(calls["delta"] - 0.25).abs().argmin()]
    p = puts.iloc[(puts["delta"] + 0.25).abs().argmin()]
    return float(p["iv"] - c["iv"])


def extract_features(client: CVForgeClient, sym: str) -> dict | None:
    """Raw daily features for one name; ``None`` if the core chain/exposures fail.

    Core positioning (chain -> exposures + skew) is required. The enrichment pulls
    (RV history, RSI, SMA) degrade to None on a transient 502 rather than dropping
    the name (matches ``swing_report.analyze``).
    """
    try:
        chain = client.chain(sym)
        exp = client.exposures(sym, chain=chain)
    except DataSourceError:
        return None
    if not exp:
        return None
    spot = exp.get("spot")
    atm_iv = exp.get("atm_iv")

    frm = (date.today() - timedelta(days=180)).isoformat()
    bars = _safe(lambda: client.aggs(sym, frm=frm, to=date.today().isoformat()))
    rv = (
        realized_vol(bars["c"].to_numpy(dtype=float))
        if (bars is not None and not bars.empty)
        else None
    )
    rsi_raw = _safe(
        lambda: client.fmp(
            "technical-indicators/rsi", {"symbol": sym, "periodLength": 14, "timeframe": "1day"}
        )
    )
    rsi = float(rsi_raw[0]["rsi"]) if isinstance(rsi_raw, list) and rsi_raw else None
    sma_raw = _safe(
        lambda: client.fmp(
            "technical-indicators/sma", {"symbol": sym, "periodLength": 50, "timeframe": "1day"}
        )
    )
    sma50 = float(sma_raw[0]["sma"]) if isinstance(sma_raw, list) and sma_raw else None
    return {
        "spot": spot,
        "atm_iv": atm_iv,
        "rv20": rv,
        "iv_rv": (atm_iv / rv) if (rv and atm_iv) else None,
        "rsi14": rsi,
        "sma50": sma50,
        "px_vs_sma50": (spot / sma50 - 1.0) if (spot and sma50) else None,
        "skew_25d": skew_25d(chain),
        "gex": exp.get("gex_total"),
        "dex": exp.get("dex_total"),
    }


def _history(session: Session, symbol: str, *, before: date) -> dict[str, list[float]]:
    """Trailing per-feature series (ts < ``before``, oldest first) for percentiles.

    Read before the upsert so today's row never contaminates its own percentile.
    """
    rows = session.execute(
        select(
            SwingFeature.atm_iv,
            SwingFeature.iv_rv,
            SwingFeature.skew_25d,
            SwingFeature.gex,
            SwingFeature.dex,
        )
        .where(SwingFeature.symbol == symbol, SwingFeature.ts < before)
        .order_by(SwingFeature.ts.asc())
    ).all()
    hist: dict[str, list[float]] = {k: [] for k in _RANKED}
    for row in rows:
        for key, val in zip(_RANKED, row, strict=True):
            if val is not None:
                hist[key].append(float(val))
    return hist


def build_rows(
    session: Session, client: CVForgeClient, symbols: list[str], *, as_of: date
) -> list[dict]:
    """Today's ``swing_features`` rows: raw features + trailing-252d percentiles."""
    records: list[dict] = []
    for sym in symbols:
        feats = extract_features(client, sym)
        if feats is None or feats.get("spot") is None:
            continue
        hist = _history(session, sym, before=as_of)
        rec: dict = {"symbol": sym, "ts": as_of, **feats}
        for feat in _RANKED:
            val = feats[feat]
            trailing = hist[feat][-252:]
            rec[_PCTILE_COL[feat]] = skew_percentile(trailing, val) if val is not None else None
        records.append(rec)
    return records


def _upsert(session: Session, records: list[dict]) -> None:
    """Idempotent upsert into ``swing_features`` (refresh on the (symbol, ts) key)."""
    if not records:
        return
    stmt = pg_insert(SwingFeature).values(records)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts"],
        set_={c: stmt.excluded[c] for c in _UPDATE_COLS},
    )
    session.execute(stmt)


def run(
    session: Session, *, settings: Settings | None = None, symbols: list[str] | None = None
) -> None:
    """Build today's swing_features rows and upsert them (idempotent)."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="swing_features")
    syms = symbols or settings.watchlist_symbols
    as_of = eastern_now().date()
    client = CVForgeClient(settings)
    records: list[dict] = []
    try:
        records = build_rows(session, client, syms, as_of=as_of)
        _upsert(session, records)
        session.commit()
    finally:
        client.close()
    bound.info(
        "swing_features.done",
        as_of=as_of.isoformat(),
        rows=len(records),
        symbols=len({r["symbol"] for r in records}),
    )


def main() -> None:
    """Manual/CLI + NAS entrypoint; optional positional symbols override the watchlist."""
    from trading_intel.memory.db import make_session_factory

    parser = argparse.ArgumentParser(description="Daily swing feature-snapshot collector.")
    parser.add_argument("symbols", nargs="*", help="tickers (default: WATCHLIST from .env)")
    args = parser.parse_args()

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    symbols = [s.upper() for s in args.symbols] or None
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, settings=settings, symbols=symbols)


if __name__ == "__main__":
    main()
