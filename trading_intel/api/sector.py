"""Sector lead/lag report assembly — DB reader (no Convex; CVForge + yfinance).

Mirrors ``api.positioning``: pure assembly (``assemble_sector``) split from the
DB / vendor reads (``build_sector``). Per-SPDR net GEX/DEX/flip/dex_flip/ATM-IV
come from the ``greeks_snapshots`` rows the ``sector_greeks`` job fills from
CVForge (source ``cvforge``) — so the sector layer never spends the Convex
10/min budget (rule 1). Correlation comes from ``sector_corr_snapshots``;
momentum + the SPDR-vs-SPY internals read come from the free yfinance daily
history. IV percentile is built from each SPDR's own ATM-IV history and reads
``pending`` until enough snapshots have accrued.

Descriptor only — FlashAlpha rule 4. The ranking + flags live in the pure
``market.sector_scan``; nothing here emits a signal.
"""
from __future__ import annotations

from datetime import datetime

_SPY = "SPY"


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _pctile(values: list[float], latest: float) -> float | None:
    """Fraction of ``values`` <= ``latest`` (0..1). None if too few samples."""
    xs = [v for v in values if v is not None]
    if len(xs) < 20:
        return None
    below = sum(1 for v in xs if v <= latest)
    return round(below / len(xs), 4)


def _latest_greeks(session, symbol: str) -> dict:
    """Newest greeks_snapshots row for ``symbol`` (any source; prefers most recent ts)."""
    from sqlalchemy import select

    from trading_intel.memory.models import GreeksSnapshot

    row = session.execute(
        select(GreeksSnapshot)
        .where(GreeksSnapshot.symbol == symbol)
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "symbol": symbol,
        "spot": _num(getattr(row, "spot", None)),
        "gex_total": _num(getattr(row, "gex_total", None)),
        "dex_total": _num(getattr(row, "dex_total", None)),
        "gex_flip": _num(getattr(row, "gex_flip", None)),
        "dex_flip": _num(getattr(row, "dex_flip", None)),
        "atm_iv": _num(getattr(row, "atm_iv", None)),
        "ts": getattr(row, "ts", None),
        "source": getattr(row, "source", None),
    }


def _iv_percentile(session, symbol: str, *, lookback: int = 252) -> float | None:
    """Percentile rank of the latest ATM IV within the symbol's own recent history."""
    from sqlalchemy import select

    from trading_intel.memory.models import GreeksSnapshot

    rows = session.execute(
        select(GreeksSnapshot.atm_iv)
        .where(GreeksSnapshot.symbol == symbol, GreeksSnapshot.atm_iv.is_not(None))
        .order_by(GreeksSnapshot.ts.desc())
        .limit(lookback)
    ).scalars().all()
    vals = [float(v) for v in rows if v is not None]
    if len(vals) < 20:
        return None
    return _pctile(vals, vals[0])  # vals[0] is the newest (desc order)


def _close_series(source, symbol: str, *, period: str = "6mo") -> list[float]:
    """Ascending daily closes for ``symbol`` (empty on any failure)."""
    try:
        hist = source.daily_history(symbol, period=period)
    except Exception:  # noqa: BLE001 — best-effort; a bad ticker shouldn't kill the report
        return []
    if hist is None or getattr(hist, "empty", True) or "close" not in hist.columns:
        return []
    return [float(c) for c in hist["close"].tolist() if c is not None]


def _ret(closes: list[float], n: int) -> float | None:
    """Simple return over the last ``n`` sessions."""
    if len(closes) <= n or closes[-n - 1] == 0:
        return None
    return closes[-1] / closes[-n - 1] - 1.0


def _wide_close_frame(source, symbols, *, period: str = "1y"):
    """Wide daily-close frame (index=date, cols=symbol) for correlation/breadth trends."""
    import pandas as pd

    cols = {}
    for sym in symbols:
        try:
            hist = source.daily_history(sym, period=period)
        except Exception:  # noqa: BLE001 — best-effort per ticker
            continue
        if hist is None or getattr(hist, "empty", True) or "close" not in hist.columns:
            continue
        cols[sym] = hist.set_index("date")["close"]
    return pd.DataFrame(cols).sort_index() if cols else pd.DataFrame()


def _price_trends(frame, *, n: int = 30) -> dict:
    """21d/63d avg PAIRWISE correlation, dispersion, and sector-breadth trends,
    computed directly from the SPDR price frame — immediate, no DB accrual. The
    last ``n`` sessions are returned as parallel lists (with ``dates``)."""
    import numpy as np
    import pandas as pd

    from trading_intel.market.sector_correlation import (
        avg_pairwise_corr,
        compute_returns,
        cross_sectional_dispersion,
    )

    out = {"dates": [], "corr21": [], "corr63": [], "dispersion": [], "breadth": []}
    if frame is None or getattr(frame, "empty", True) or frame.shape[1] < 2:
        return out
    returns = compute_returns(frame).dropna(how="all")
    if returns.empty:
        return out
    c21 = avg_pairwise_corr(returns, 21)
    c63 = avg_pairwise_corr(returns, 63)
    disp = cross_sectional_dispersion(returns)
    up = (returns > 0).sum(axis=1)
    tot = returns.notna().sum(axis=1)
    breadth = up / tot.replace(0, np.nan)
    idx = returns.index[-n:]

    def _tl(s):
        return [None if pd.isna(s.get(d)) else round(float(s.get(d)), 5) for d in idx]

    out["dates"] = [str(d.date()) if hasattr(d, "date") else str(d) for d in idx]
    out["corr21"], out["corr63"] = _tl(c21), _tl(c63)
    out["dispersion"], out["breadth"] = _tl(disp), _tl(breadth)
    return out


def _corr_head(trends: dict) -> dict:
    """Latest correlation regime from the price-derived trend (headline + gate)."""
    from trading_intel.market.sector_correlation import corr_regime

    def _last(lst):
        for v in reversed(lst or []):
            if v is not None:
                return v
        return None

    a21 = _last(trends.get("corr21"))
    a63 = _last(trends.get("corr63"))
    return {
        "avg_corr": {"21d": a21, "63d": a63},
        "regime": {"21d": corr_regime(a21), "63d": corr_regime(a63)},
        "dispersion": _last(trends.get("dispersion")),
        "as_of": trends["dates"][-1] if trends.get("dates") else None,
    }


def _rr25_trend(session, symbol: str, *, n: int = 20) -> dict:
    """rr25 history (ascending) + the latest day-over-day shift, from sector_snapshots.

    A FALLING rr25 (put IV − call IV shrinking) = demand rotating to the call
    side = the bullish LEAP-call tell. ``rr25_shift`` is latest − prior.
    """
    from sqlalchemy import select

    from trading_intel.memory.models import SectorSnapshot

    rows = session.execute(
        select(SectorSnapshot.as_of, SectorSnapshot.rr25)
        .where(SectorSnapshot.symbol == symbol)
        .order_by(SectorSnapshot.as_of.asc())
    ).all()
    series = [_num(r) for _a, r in rows if r is not None][-n:]
    shift = (series[-1] - series[-2]) if len(series) >= 2 else None
    return {"rr25_trend": series, "rr25_shift": shift}


def _latest_sector_extras(session, symbol: str) -> dict:
    """Layer-2 extras for one SPDR: 25Δ skew + walls + the fixed-strike footprint.

    Reads the two most recent ``sector_snapshots`` rows (latest + prior day) so the
    strike-IV grids can be diffed into the offered/bid footprint. Everything is
    None/pending-safe until the ``sector_greeks`` job has run (≥2 days for the
    footprint).
    """
    from sqlalchemy import select

    from trading_intel.greeks.skew_walls import fixed_strike_footprint
    from trading_intel.memory.models import SectorSnapshot

    recent = session.execute(
        select(SectorSnapshot)
        .where(SectorSnapshot.symbol == symbol)
        .order_by(SectorSnapshot.as_of.desc())
        .limit(2)
    ).scalars().all()
    if not recent:
        return {
            "rr25": None, "rr25_dte": None, "call_wall": None, "put_wall": None,
            "footprint": {"pending": True, "read": None, "offered": 0, "bid": 0, "flat": 0},
        }
    latest = recent[0]
    prior = recent[1] if len(recent) > 1 else None
    fp = fixed_strike_footprint(
        getattr(latest, "strike_iv", None),
        getattr(prior, "strike_iv", None) if prior is not None else None,
    )
    return {
        "rr25": _num(getattr(latest, "rr25", None)),
        "rr25_dte": getattr(latest, "rr25_dte", None),
        "call_wall": _num(getattr(latest, "call_wall", None)),
        "put_wall": _num(getattr(latest, "put_wall", None)),
        "footprint": fp,
    }


def assemble_sector(rows: list[dict], *, corr: dict, internals: dict, as_of: str | None) -> dict:
    """Pure: run the sector scan and wrap it with report metadata."""
    from trading_intel.market.sector_scan import build_sector_scan

    scan = build_sector_scan(rows, corr=corr, internals=internals)
    scan["as_of"] = as_of or datetime.now().isoformat(timespec="seconds")
    scan["meta"] = {
        "source": "cvforge-db + yfinance",
        "n_sectors": scan.get("n_sectors", len(rows)),
        "n_priced": sum(1 for r in rows if r.get("gex_total") is not None),
    }
    return scan


def build_sector(session, settings=None) -> dict:
    """Assemble the sector lead/lag payload from the DB + free price history."""
    from trading_intel.clients.prices import YFinancePriceSource
    from trading_intel.config import get_settings
    from trading_intel.market.sector_correlation import SECTOR_SPDRS
    from trading_intel.market.sector_scan import internals_health

    settings = settings or get_settings()
    roots = list(getattr(settings, "sector_roots", None) or SECTOR_SPDRS)

    source = YFinancePriceSource()
    frame = _wide_close_frame(source, roots, period="1y")

    spdr_today: dict[str, float | None] = {}
    rows: list[dict] = []
    latest_ts = None
    for sym in roots:
        if not getattr(frame, "empty", True) and sym in frame.columns:
            closes = [float(x) for x in frame[sym].dropna().tolist()]
        else:
            closes = _close_series(source, sym)
        spdr_today[sym] = (closes[-1] / closes[-2] - 1.0) if (len(closes) >= 2 and closes[-2]) else None
        g = _latest_greeks(session, sym)
        row = {
            "symbol": sym,
            "spot": g.get("spot") or (closes[-1] if closes else None),
            "gex_total": g.get("gex_total"),
            "dex_total": g.get("dex_total"),
            "gex_flip": g.get("gex_flip"),
            "dex_flip": g.get("dex_flip"),
            "atm_iv": g.get("atm_iv"),
            "iv_pctile": _iv_percentile(session, sym),
            "ret_21d": _ret(closes, 21),
            "ret_63d": _ret(closes, 63),
        }
        row.update(_latest_sector_extras(session, sym))  # rr25 + walls + fixed-strike footprint
        row.update(_rr25_trend(session, sym))  # rr25 history + day-over-day shift
        rows.append(row)
        if g.get("ts") is not None and (latest_ts is None or g["ts"] > latest_ts):
            latest_ts = g["ts"]

    # Correlation + dispersion + breadth trends straight from prices (immediate).
    trends = _price_trends(frame)
    corr = _corr_head(trends)

    spy_closes = _close_series(source, _SPY)
    index_dir = (spy_closes[-1] / spy_closes[-2] - 1.0) if (len(spy_closes) >= 2 and spy_closes[-2]) else None
    internals = internals_health(spdr_today, index_dir)
    internals["trend"] = trends.get("breadth")

    as_of = (latest_ts.isoformat() if latest_ts else None) or corr.get("as_of")
    payload = assemble_sector(rows, corr=corr, internals=internals, as_of=as_of)
    payload["trends"] = trends
    return payload
