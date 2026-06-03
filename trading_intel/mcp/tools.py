"""MCP tool functions — pure adapters over the existing data layer.

Each function takes a ``Session`` (and where relevant, an ``LLMProvider``)
plus its arguments and returns a JSON-serialisable dict. FastMCP decoration
is applied in ``server.py``; keeping the bodies plain makes them trivially
unit-testable with a SQLite ``Session`` + the existing ``StubLLM`` pattern.

Tools are READ-ONLY by design (FlashAlpha rule 4 + rule 1 spirit). They
never call ConvexValue, never write to ``signals``, and never persist
anything. Anything stored already came in via a scheduled job.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import structlog
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings
from trading_intel.dashboard.am_report_data import (
    am_summary_by_date,
    available_dates,
    latest_am_summary,
)
from trading_intel.dashboard.chart_data import load_ohlc
from trading_intel.dashboard.flow_data import load_watchlist_flow
from trading_intel.dashboard.skew_data import per_name_timeseries
from trading_intel.dashboard.ticker_data import load_latest_chain, load_snapshot_history
from trading_intel.dashboard.watchlist_metrics import (
    flip_distance,
    gamma_regime,
    load_watchlist_metrics,
)
from trading_intel.errors import ComputationError
from trading_intel.greeks.walls import compute_walls
from trading_intel.mcp.report_html import render_html_report
from trading_intel.memory.retrieval import retrieve_chunks
from trading_intel.prices import technicals as technicals_mod
from trading_intel.synthesis.am_summary import build_am_context, render_am_markdown
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_DEFAULT_HISTORY_DAYS = 30
_DEFAULT_WEEKLY_DAYS = 7
_MAX_K = 20


def _normalise_symbols(
    session: Session, symbols: list[str] | None, settings: Settings
) -> list[str]:
    """Default to the effective watchlist; uppercase + dedupe what the caller passed."""
    if not symbols:
        return effective_symbols(session, settings)
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        u = s.strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def get_latest_am_summary(session: Session) -> dict[str, Any]:
    """Return the most recent stored AM regime report (markdown + metadata).

    Reads ``am_summaries`` only; never re-runs the LLM. Use
    ``rebuild_am_summary`` for that.
    """
    row = latest_am_summary(session)
    if row is None:
        return {"date": None, "markdown": None, "metadata": None, "found": False}
    return {
        "date": row.date.isoformat(),
        "markdown": row.markdown,
        "metadata": row.metadata_json,
        "found": True,
    }


def get_am_summary_by_date(session: Session, day: str) -> dict[str, Any]:
    """Return the AM report for an ISO date string (``YYYY-MM-DD``)."""
    try:
        d = date.fromisoformat(day)
    except ValueError as exc:
        return {"error": f"invalid date: {exc}", "found": False}
    row = am_summary_by_date(session, d)
    if row is None:
        return {"date": day, "found": False, "markdown": None}
    return {
        "date": row.date.isoformat(),
        "markdown": row.markdown,
        "metadata": row.metadata_json,
        "found": True,
    }


def list_am_summary_dates(session: Session, limit: int = 30) -> dict[str, Any]:
    """Return up to ``limit`` recent AM-report dates (newest first)."""
    limit = max(1, min(int(limit), 365))
    dates = available_dates(session)[:limit]
    return {"dates": [d.isoformat() for d in dates], "count": len(dates)}


def get_watchlist_regime(
    session: Session,
    symbols: list[str] | None = None,
    *,
    settings: Settings,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    weekly_days: int = _DEFAULT_WEEKLY_DAYS,
) -> dict[str, Any]:
    """Watchlist regime-descriptor table (one row per symbol).

    Thin wrapper over ``dashboard.watchlist_metrics.load_watchlist_metrics``.
    Descriptors only — never a signal (FlashAlpha rule 4).
    """
    syms = _normalise_symbols(session, symbols, settings)
    df = load_watchlist_metrics(
        session, syms, weekly_days=int(weekly_days), history_days=int(history_days)
    )
    if df is None or df.empty:
        return {"symbols": syms, "rows": [], "count": 0}
    rows = df.to_dict(orient="records")
    return {"symbols": syms, "rows": rows, "count": len(rows)}


def get_watchlist_flow(
    session: Session,
    symbols: list[str] | None = None,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Latest flow-snapshot row per symbol (descriptive)."""
    syms = _normalise_symbols(session, symbols, settings)
    df = load_watchlist_flow(session, syms)
    if df is None or df.empty:
        return {"symbols": syms, "rows": [], "count": 0}
    rows = df.to_dict(orient="records")
    return {"symbols": syms, "rows": rows, "count": len(rows)}


def rebuild_am_summary(
    session: Session,
    llm: LLMProvider,
    *,
    settings: Settings,
) -> dict[str, Any]:
    """Re-render today's AM summary against current stored data (no DB write).

    Returns the same markdown + metadata pair the scheduler job produces, but
    does NOT upsert into ``am_summaries`` — that's the scheduled job's
    responsibility (idempotent ON CONFLICT, rule 5). Use this for ad-hoc
    "what would the brief look like right now?" queries from Claude Desktop.
    """
    ctx = build_am_context(session, settings)
    markdown, metadata = render_am_markdown(ctx, llm, settings)
    return {
        "date": ctx.as_of.isoformat(),
        "symbols": ctx.watchlist,
        "research_tickers": [r.symbol for r in ctx.research],
        "markdown": markdown,
        "metadata": metadata,
    }


def search_knowledge(
    session: Session,
    llm: LLMProvider,
    query: str,
    *,
    kind: str = "methodology",
    k: int = 6,
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Semantic search over the PDF/docx knowledge base (pgvector).

    ``kind`` is ``methodology`` (desk frameworks) or ``research`` (company
    material). ``symbols`` restricts to chunks tagged with any of the given
    tickers — only meaningful for ``research``.
    """
    k_clamped = max(1, min(int(k), _MAX_K))
    if kind not in {"methodology", "research"}:
        return {"error": f"invalid kind: {kind!r}", "hits": []}
    syms = [s.strip().upper() for s in (symbols or []) if s and s.strip()]
    hits = retrieve_chunks(session, llm, query, k=k_clamped, kind=kind, symbols=syms or None)
    return {
        "query": query,
        "kind": kind,
        "k": k_clamped,
        "symbols": syms or None,
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "title": h.title,
                "text": h.text,
                "distance": h.distance,
            }
            for h in hits
        ],
        "count": len(hits),
    }


def get_gamma_history(session: Session, symbol: str, *, days: int = 30) -> dict[str, Any]:
    """Per-day net-GEX / flip / regime series for one ticker (descriptive).

    Reads ``greeks_snapshots`` via ``load_snapshot_history``; returns a compact
    daily series plus a summary (current vs start, direction). Regime descriptors
    only - never a signal (FlashAlpha rule 4).
    """
    days_c = max(2, min(int(days), 365))
    sym = symbol.strip().upper()
    df = load_snapshot_history(session, sym, days=days_c)
    if df is None or df.empty:
        return {"symbol": sym, "rows": [], "count": 0, "found": False}

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        spot = _num(r.get("spot"))
        flip = _num(r.get("gex_flip"))
        rows.append(
            {
                "date": _iso_day(r.get("ts")),
                "spot": spot,
                "gex_total": _num(r.get("gex_total")),
                "dex_total": _num(r.get("dex_total")),
                "vex_total": _num(r.get("vex_total")),
                "chex_total": _num(r.get("chex_total")),
                "gex_flip": flip,
                "regime": gamma_regime(spot, flip),
                "flip_dist": flip_distance(spot, flip),
                "atm_iv": _num(r.get("atm_iv")),
            }
        )

    gex_vals = [x["gex_total"] for x in rows if x["gex_total"] is not None]
    summary = None
    if gex_vals:
        first, last = gex_vals[0], gex_vals[-1]
        change = last - first
        summary = {
            "current_gex": last,
            "start_gex": first,
            "change": change,
            "direction": "up" if change > 0 else "down" if change < 0 else "flat",
            "current_regime": rows[-1]["regime"],
        }
    return {"symbol": sym, "rows": rows, "count": len(rows), "summary": summary, "found": True}


def get_technicals(session: Session, symbol: str, *, days: int = 120) -> dict[str, Any]:
    """Latest technical indicators + candlestick patterns for one ticker.

    Pulls OHLC from ``quotes_daily`` and computes RSI / SMA / EMA (pure pandas)
    plus MACD / Bollinger / ATR (via the ``ta`` library). If ``ta`` is not
    installed those fields are omitted and a note is returned. Descriptive
    indicators only - rule 4.
    """
    sym = symbol.strip().upper()
    days_c = max(20, min(int(days), 750))
    ohlc = load_ohlc(session, sym, days=days_c)
    if ohlc is None or ohlc.empty:
        return {"symbol": sym, "found": False, "indicators": None}

    close = ohlc["close"].astype(float)
    latest = ohlc.iloc[-1]
    ind: dict[str, Any] = {
        "close": _num(latest.get("close")),
        "rsi14": _last(technicals_mod.rsi(close, 14)),
        "sma20": _last(technicals_mod.sma(close, 20)),
        "sma50": _last(technicals_mod.sma(close, 50)),
        "ema21": _last(technicals_mod.ema(close, 21)),
    }
    notes: list[str] = []
    try:
        macd_df = technicals_mod.macd(close)
        ind["macd"] = _last(macd_df["macd"])
        ind["macd_signal"] = _last(macd_df["signal"])
        ind["macd_hist"] = _last(macd_df["hist"])
        bb = technicals_mod.bollinger(close)
        ind["bb_upper"] = _last(bb["upper"])
        ind["bb_lower"] = _last(bb["lower"])
        ind["bb_pctb"] = _last(bb["pctb"])
        ind["atr14"] = _last(
            technicals_mod.atr(
                ohlc["high"].astype(float), ohlc["low"].astype(float), close, period=14
            )
        )
    except ImportError as exc:
        notes.append(str(exc))

    patterns = technicals_mod.candlestick_patterns(ohlc)
    active = sorted(k for k, v in patterns.items() if v)
    return {
        "symbol": sym,
        "as_of": _iso_day(latest.get("date")),
        "bars": len(ohlc),
        "indicators": ind,
        "candlestick_patterns": active,
        "notes": notes or None,
        "found": True,
    }


def get_skew_history(
    session: Session, symbol: str, *, horizon_dte: int = 30, days: int = 60
) -> dict[str, Any]:
    """Full volatility-skew series for one ticker (risk-reversals + butterflies).

    Reads ``skew_snapshots`` directly: 10D/25D risk-reversals (``rr`` — put IV minus
    call IV; positive = downside puts bid / fear, negative = upside calls bid) and
    butterflies (``bf`` — wing-vs-ATM convexity), their trailing percentiles
    (63d/252d), the front-vs-back RR slope, the name's 60d VIX beta, the abnormal
    RR (residual after removing the VIX-beta-predicted move), the shift-vs-slide
    label and the descriptive summary ``label``. Descriptive only — rule 4.
    """
    from datetime import timedelta

    from sqlalchemy import select as _select

    from trading_intel.memory.models import SkewSnapshot

    sym = symbol.strip().upper()
    hz = int(horizon_dte)
    days_c = max(5, min(int(days), 365))
    start = date.today() - timedelta(days=days_c)
    skew_rows = (
        session.execute(
            _select(SkewSnapshot)
            .where(
                SkewSnapshot.symbol == sym,
                SkewSnapshot.horizon_dte == hz,
                SkewSnapshot.ts >= start,
            )
            .order_by(SkewSnapshot.ts.asc())
        )
        .scalars()
        .all()
    )
    if not skew_rows:
        return {
            "symbol": sym,
            "horizon_dte": hz,
            "rows": [],
            "count": 0,
            "found": False,
        }
    rows: list[dict[str, Any]] = []
    for r in skew_rows:
        rows.append(
            {
                "date": _iso_day(r.ts),
                "atm_iv": _num(r.atm_iv),
                "rr_10d": _num(r.rr_10d),
                "rr_25d": _num(r.rr_25d),
                "bf_10d": _num(r.bf_10d),
                "bf_25d": _num(r.bf_25d),
                "rr_25d_pctile_63d": _num(r.rr_25d_pctile_63d),
                "rr_25d_pctile_252d": _num(r.rr_25d_pctile_252d),
                "bf_25d_pctile_252d": _num(r.bf_25d_pctile_252d),
                "front_back_rr_slope": _num(r.front_back_rr_slope),
                "vix_beta_60d": _num(r.vix_beta_60d),
                "rr_25d_abnormal": _num(r.rr_25d_abnormal),
                "shift_slide_label": r.shift_slide_label,
                "label": r.label,
            }
        )
    rr_vals = [x["rr_25d"] for x in rows if x["rr_25d"] is not None]
    summary = None
    if rr_vals:
        last = rr_vals[-1]
        summary = {
            "current_rr_25d": last,
            "current_pctile_252d": rows[-1]["rr_25d_pctile_252d"],
            "current_bf_25d": rows[-1]["bf_25d"],
            "rr_25d_abnormal": rows[-1]["rr_25d_abnormal"],
            "label": rows[-1]["label"],
            "bias": (
                "downside puts bid (fear)"
                if last > 0
                else "upside calls bid" if last < 0 else "flat"
            ),
        }
    return {
        "symbol": sym,
        "horizon_dte": hz,
        "rows": rows,
        "count": len(rows),
        "summary": summary,
        "found": True,
    }


def render_report_html(session: Session, symbol: str, *, days: int = 180) -> dict[str, Any]:
    """Write a standalone HTML chart (price + gamma levels + GEX + skew); return its path.

    Positioning-first: candlestick + volume with the gamma flip and call/put walls
    overlaid, net-GEX history, and the 25d risk-reversal skew line. No DB write -
    just reads stored data and renders a plotly file. Descriptive only - rule 4.
    """
    sym = symbol.strip().upper()
    days_c = max(20, min(int(days), 750))
    ohlc = load_ohlc(session, sym, days=days_c)
    gamma = load_snapshot_history(session, sym, days=days_c)
    skew = per_name_timeseries(session, sym, horizon_dte=30, lookback_days=days_c)
    walls: dict[str, Any] = {}
    try:
        _, chain = load_latest_chain(session, sym)
        if chain is not None and not chain.empty:
            walls = compute_walls(chain)
    except ComputationError:
        walls = {}
    try:
        path = render_html_report(sym, ohlc, gamma, walls=walls, skew=skew)
    except ValueError as exc:
        return {"symbol": sym, "found": False, "error": str(exc)}
    return {"symbol": sym, "path": path, "found": True}


def get_time_and_sales(
    source: OptionsDataSource,
    symbol: str | None = None,
    *,
    limit: int = 50,
    day: int = 0,
) -> dict[str, Any]:
    """Top option prints from the MARKET-WIDE tape, sorted by premium.

    Time & sales is a whole-market feed: ``symbol=None`` returns the biggest
    prints across every name (each row's ``ticker`` says which), e.g.
    ``.CVS260702C92`` -> CVS / 2026-07-02 / call / strike 92. Pass a ``symbol``
    to filter to one root. Live only during market hours; after the close the
    tape returns zeroed fields and prior sessions aren't served. Descriptive
    (the raw tape), never a signal - rule 4.
    """
    sym = symbol.strip().upper() if symbol else None
    limit_c = max(1, min(int(limit), 500))
    df = source.time_and_sales(sym, limit=limit_c, day=int(day))
    scope = sym or "MARKET"
    if df is None or df.empty:
        return {"scope": scope, "day": int(day), "rows": [], "count": 0, "found": False}

    keep = [
        "time",
        "root",
        "opt_kind",
        "strike",
        "expiration",
        "price",
        "size",
        "premium",
        "aggressor_side",
        "iv",
        "delta",
        "spot",
    ]
    sub = df[[c for c in keep if c in df.columns]].copy()
    if "premium" in sub.columns:
        sub = sub.sort_values("premium", ascending=False, na_position="last")

    rows: list[dict[str, Any]] = []
    for _, r in sub.head(limit_c).iterrows():
        rows.append(
            {
                "time": _iso_ts(r.get("time")),
                "ticker": r.get("root"),
                "kind": r.get("opt_kind"),
                "strike": _num(r.get("strike")),
                "expiry": _iso_day(r.get("expiration")),
                "price": _num(r.get("price")),
                "size": _num(r.get("size")),
                "premium": _num(r.get("premium")),
                "side": r.get("aggressor_side"),
                "iv": _num(r.get("iv")),
                "delta": _num(r.get("delta")),
            }
        )

    prem = pd.to_numeric(sub["premium"], errors="coerce") if "premium" in sub.columns else None
    live = bool(prem is not None and float(prem.fillna(0).abs().sum()) > 0)
    note = None
    if not live:
        note = (
            "all trade fields are zero - this is an after-hours/EOD snapshot, not "
            "live prints. The tape is live-only; re-run during market hours."
        )
    return {
        "scope": scope,
        "day": int(day),
        "rows": rows,
        "count": len(rows),
        "live_prints": live,
        "note": note,
        "found": True,
    }


def _num(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


def _iso_ts(value: object) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except (ValueError, TypeError):
        return str(value)


def _last(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.iloc[-1]) if not s.empty else None


def _iso_day(value: object) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).date().isoformat()
    except (ValueError, TypeError):
        return str(value)
