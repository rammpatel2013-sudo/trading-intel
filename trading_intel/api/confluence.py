"""Confluence read-out — DB loader + file store (read-only over banked tables).

Fills ``market.confluence_build.ConfluenceInputs`` for one session from tables the
collectors already bank — no vendor calls, no writes to the DB:

    oi_chain_eod        per-strike SPX book (OI, oi_change, iv), newest ts on/before the day
    greeks_snapshots    spot (same source get_walls / get_profile use); quotes_daily fallback
    quotes_daily        SPX + SPY daily OHLCV (price memory, anchored VWAP via SPY volume)
    vix_data            VIX close + history (measured spot-vol beta)
    vix_options_chain   VIX option OI per expiry/strike
    vol_surface_cm      constant-maturity smile (22/25-delta strikes)
    tas_prints          source='convex_index', SPX/SPXW roots, last 3 sessions

The SPX book is the 08:35 morning snapshot (``index_walls_am``): its OI is the OCC
open interest as of the PRIOR close. Read-outs are stored as JSON under
``reports/confluence/confluence_YYYYMMDD.json``. Positioning read, not a trade signal
(rule 4).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.market.confluence import DEFAULT_WINDOW
from trading_intel.market.confluence_build import ConfluenceInputs, compute_readout
from trading_intel.market.confluence_layers import (
    Bar,
    BigTradeLeg,
    ChainRow,
    SmilePoint,
    VixOption,
)
from trading_intel.memory.models import (
    GreeksSnapshot,
    OiChainEod,
    QuoteDaily,
    TasPrint,
    VixData,
    VixOptionsChain,
    VolSurfaceCM,
)
from trading_intel.timeutils import is_trading_session, live_expiry_floor

SYMBOL = "SPX"
CHAIN_DTE_MAX = 45
OUT_DIR = Path(__file__).resolve().parents[2] / "reports" / "confluence"


def _end(d: date) -> datetime:
    return datetime.combine(d + timedelta(days=1), time.min)


def book_ts(session: Session, as_of: date | None = None) -> datetime | None:
    """Newest SPX ``oi_chain_eod`` ts on/before ``as_of`` (any convex* source)."""
    q = select(OiChainEod.ts).where(OiChainEod.symbol == SYMBOL, OiChainEod.source.like("convex%"))
    if as_of is not None:
        q = q.where(OiChainEod.ts < _end(as_of))
    return session.execute(q.order_by(OiChainEod.ts.desc()).limit(1)).scalar_one_or_none()


def book_days(session: Session, *, since: date | None = None) -> list[date]:
    """Distinct SPX book days (oldest first) — the backfill calendar."""
    q = select(OiChainEod.ts).where(OiChainEod.symbol == SYMBOL,
                                    OiChainEod.source.like("convex%")).distinct()
    days = sorted({t.date() if isinstance(t, datetime) else t
                   for t in session.execute(q).scalars()})
    return [d for d in days if (since is None or d >= since) and is_trading_session(d)]


def _chain(session: Session, ts: datetime) -> list[ChainRow]:
    rows = session.execute(
        select(OiChainEod.strike, OiChainEod.cp, OiChainEod.dte, OiChainEod.iv, OiChainEod.oi,
               OiChainEod.oi_change, OiChainEod.expiry)
        .where(OiChainEod.symbol == SYMBOL, OiChainEod.source.like("convex%"),
               OiChainEod.ts == ts, OiChainEod.dte <= CHAIN_DTE_MAX,
               OiChainEod.expiry >= live_expiry_floor(ts.date()))
    ).all()
    return [ChainRow(float(r.strike), str(r.cp).upper()[:1], int(r.dte or 0), r.iv, r.oi,
                     r.oi_change, r.expiry) for r in rows if r.strike is not None]


def _spot(session: Session, day: date) -> float | None:
    row = session.execute(
        select(GreeksSnapshot.spot)
        .where(GreeksSnapshot.symbol == SYMBOL, GreeksSnapshot.source == "convex",
               GreeksSnapshot.ts < _end(day), GreeksSnapshot.ts >= datetime.combine(day, time.min))
        .order_by(GreeksSnapshot.ts.desc()).limit(1)
    ).first()
    if row and row[0]:
        return float(row[0])
    q = session.execute(
        select(QuoteDaily.close).where(QuoteDaily.symbol == SYMBOL, QuoteDaily.date <= day)
        .order_by(QuoteDaily.date.desc()).limit(1)
    ).first()
    return float(q[0]) if q and q[0] else None


def _bars(session: Session, sym: str, day: date, n: int = 400) -> list[Bar]:
    rows = session.execute(
        select(QuoteDaily.date, QuoteDaily.open, QuoteDaily.high, QuoteDaily.low,
               QuoteDaily.close, QuoteDaily.volume)
        .where(QuoteDaily.symbol == sym, QuoteDaily.date <= day)
        .order_by(QuoteDaily.date.desc()).limit(n)
    ).all()
    return [Bar(r.date, float(r.open), float(r.high), float(r.low), float(r.close),
                float(r.volume) if r.volume else None) for r in reversed(rows)
            if None not in (r.open, r.high, r.low, r.close)]


def _vix(session: Session, day: date) -> tuple[float | None, list[tuple[date, float]]]:
    rows = session.execute(
        select(VixData.date, VixData.vix).where(VixData.date <= day)
        .order_by(VixData.date.desc()).limit(80)
    ).all()
    hist = [(r.date, float(r.vix)) for r in reversed(rows) if r.vix]
    spot = next((v for d, v in reversed(hist) if d == day), None)
    return spot, hist


def _vix_options(session: Session, day: date) -> list[VixOption]:
    ts = session.execute(
        select(func.max(VixOptionsChain.ts)).where(VixOptionsChain.ts <= day)
    ).scalar()
    if ts is None or (day - ts).days > 4:
        return []
    rows = session.execute(
        select(VixOptionsChain.expiration, VixOptionsChain.strike, VixOptionsChain.opt_kind,
               VixOptionsChain.oi).where(VixOptionsChain.ts == ts)
    ).all()
    return [VixOption(r.expiration, float(r.strike), str(r.opt_kind), r.oi) for r in rows]


def _smile(session: Session, day: date) -> list[SmilePoint]:
    ts = session.execute(
        select(func.max(VolSurfaceCM.ts))
        .where(VolSurfaceCM.symbol == SYMBOL, VolSurfaceCM.ts <= day)
    ).scalar()
    if ts is None or (day - ts).days > 4:
        return []
    rows = session.execute(
        select(VolSurfaceCM.dte, VolSurfaceCM.delta, VolSurfaceCM.side, VolSurfaceCM.iv,
               VolSurfaceCM.spot, VolSurfaceCM.near_expiry)
        .where(VolSurfaceCM.symbol == SYMBOL, VolSurfaceCM.ts == ts)
    ).all()
    return [SmilePoint(int(r.dte), float(r.delta), str(r.side), float(r.iv), r.spot, r.near_expiry)
            for r in rows if r.iv]


def _trades(session: Session, day: date, sessions: int = 3) -> list[BigTradeLeg]:
    days, d = [], day
    while len(days) < sessions:
        if is_trading_session(d):
            days.append(d)
        d -= timedelta(days=1)
    rows = session.execute(
        select(TasPrint).where(TasPrint.source == "convex_index",
                               TasPrint.root.in_(("SPX", "SPXW")),
                               TasPrint.trade_date.in_(days))
        .order_by(TasPrint.notional.desc()).limit(400)
    ).scalars().all()
    return [BigTradeLeg(p.trade_date, p.leg_group, float(p.strike), str(p.cp or "?"), p.expiry,
                        float(p.notional or 0.0), bool(p.is_financing)) for p in rows
            if p.strike is not None]


def load_inputs(session: Session, as_of: date | None = None) -> ConfluenceInputs | None:
    """Gather one session's inputs; None when no SPX book exists on/before ``as_of``."""
    ts = book_ts(session, as_of)
    if ts is None:
        return None
    day = as_of or ts.date()
    notes: list[str] = []
    if ts.date() != day:
        notes.append(f"no SPX book stamped {day.isoformat()}: "
                     f"using the {ts.date().isoformat()} book")
    spot = _spot(session, day)
    if spot is None:
        return None
    vix_spot, vix_hist = _vix(session, day)
    return ConfluenceInputs(
        as_of=day, spot=spot, chain=_chain(session, ts), vix_spot=vix_spot,
        vix_options=_vix_options(session, day), vix_history=vix_hist,
        spx_bars=_bars(session, SYMBOL, day), spy_bars=_bars(session, "SPY", day),
        smile=_smile(session, day), trades=_trades(session, day), notes=notes,
    )


# ── JSON store ───────────────────────────────────────────────────────────────
def json_path(day: date, out_dir: Path | None = None) -> Path:
    return (out_dir or OUT_DIR) / f"confluence_{day.strftime('%Y%m%d')}.json"


def read_json(day: date, out_dir: Path | None = None) -> dict | None:
    p = json_path(day, out_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stored_days(out_dir: Path | None = None) -> list[date]:
    d = out_dir or OUT_DIR
    out = []
    for p in sorted(d.glob("confluence_*.json")):
        try:
            out.append(datetime.strptime(p.stem.split("_")[1], "%Y%m%d").date())
        except (IndexError, ValueError):
            continue
    return out


def prior_readout(day: date, out_dir: Path | None = None) -> dict | None:
    """The newest stored read-out strictly before ``day`` (within 7 calendar days)."""
    for back in range(1, 8):
        r = read_json(day - timedelta(days=back), out_dir)
        if r is not None:
            return r
    return None


def compute_for_day(session: Session, day: date | None, *, window: float = DEFAULT_WINDOW,
                    out_dir: Path | None = None, prior: dict | None = None) -> dict | None:
    """Compute one day's read-out (prior from the store unless given). Does not write."""
    inp = load_inputs(session, day)
    if inp is None:
        return None
    if prior is None:
        prior = prior_readout(inp.as_of, out_dir)
    return compute_readout(inp, window=window, prior=prior)


def write_json(readout: dict, out_dir: Path | None = None) -> Path:
    p = json_path(date.fromisoformat(readout["as_of"]), out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(readout, indent=1, default=str), encoding="utf-8")
    return p


def get_confluence(session: Session, *, symbol: str = "SPX", day: date | None = None,
                   window: float = DEFAULT_WINDOW, out_dir: Path | None = None) -> dict[str, Any]:
    """Stored read-out for ``day`` (latest stored when None) or an on-the-fly compute."""
    if symbol.strip().upper() != SYMBOL:
        return {"found": False, "reason": "confluence read-out is built for SPX only"}
    target = day
    if target is None:
        days = stored_days(out_dir)
        ts = book_ts(session)
        newest_book = ts.date() if ts else None
        if days and (newest_book is None or days[-1] >= newest_book):
            target = days[-1]
        else:
            target = newest_book
    if target is None:
        return {"found": False, "reason": "no SPX per-strike book stored"}
    stored = read_json(target, out_dir)
    if stored is not None and float(stored.get("window", window)) == float(window):
        return {"found": True, "source": "file", **stored}
    r = compute_for_day(session, target, window=window, out_dir=out_dir)
    if r is None:
        return {"found": False, "as_of": target.isoformat(), "reason": "no SPX book or spot"}
    return {"found": True, "source": "computed", **r}
