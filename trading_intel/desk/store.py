"""Desk brief - database read / write helpers (2026-11-14). Idempotent upserts only."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from trading_intel.equity.store import upsert
from trading_intel.memory.models_desk import (
    DeskBar,
    DeskBriefDaily,
    DeskGammaDaily,
    DeskName,
    DeskPick,
    DeskUndDaily,
    FinraShortVolume,
)
from trading_intel.memory.models_equity import EquityBar, EquityProfile


def _num(x: Any) -> float | None:  # noqa: ANN401
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN -> None


# ── FINRA ───────────────────────────────────────────────────────────────
def save_finra(session: Session, day: date, short: Mapping[str, tuple[float, float]]) -> int:
    rows = [
        {"date": day, "symbol": s, "short_volume": v[0], "short_exempt": None, "total_volume": v[1]}
        for s, v in short.items()
        if s and len(s) <= 16
    ]
    return upsert(session, FinraShortVolume, rows, ["date", "symbol"], chunk=2000)


def finra_dates(session: Session, end: date, n: int = 10) -> list[date]:
    q = (
        select(FinraShortVolume.date)
        .where(FinraShortVolume.date <= end)
        .distinct()
        .order_by(FinraShortVolume.date.desc())
        .limit(n)
    )
    return list(session.execute(q).scalars().all())


def load_finra(
    session: Session, end: date, symbols: Iterable[str] | None = None, n: int = 10
) -> tuple[list[date], dict[str, list[tuple[float, float]]]]:
    """Last ``n`` FINRA sessions up to ``end``: (dates newest first, {sym: [(short, total)] newest first})."""
    dates = finra_dates(session, end, n)
    out: dict[str, list[tuple[float, float]]] = defaultdict(list)
    if not dates:
        return dates, out
    q = select(
        FinraShortVolume.symbol,
        FinraShortVolume.date,
        FinraShortVolume.short_volume,
        FinraShortVolume.total_volume,
    ).where(FinraShortVolume.date.in_(dates))
    if symbols is not None:
        q = q.where(FinraShortVolume.symbol.in_(list(symbols)))
    by: dict[str, dict[date, tuple[float, float]]] = defaultdict(dict)
    for sym, d, s, t in session.execute(q).all():
        by[sym][d] = (s or 0.0, t or 0.0)
    for sym, m in by.items():
        out[sym] = [m[d] for d in dates if d in m]  # newest first; a missing day is skipped
    return dates, out


# ── Convex und snapshot ─────────────────────────────────────────────────
UND_MAP = {
    "price": "price",
    "change": "change",
    "option_volume": "option_volume",
    "call_volume": "call_volume",
    "put_volume": "put_volume",
    "call_buy": "volm_call_buy",
    "call_sell": "volm_call_sell",
    "put_buy": "volm_put_buy",
    "put_sell": "volm_put_sell",
    "gxoi": "gxoi",
    "iv": "volatility",
}


def save_und(
    session: Session,
    day: date,
    rows: Sequence[Mapping],
    captured_at: datetime,
    *,
    split_ok: bool = True,
) -> int:
    out = []
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if not sym or len(sym) > 16:
            continue
        row = {"date": day, "symbol": sym, "captured_at": captured_at}
        row.update({k: _num(r.get(src)) for k, src in UND_MAP.items()})
        if not split_ok:  # weekend / after-reset pull: the split counters read 0, not "no buying"
            for k in ("call_buy", "call_sell", "put_buy", "put_sell"):
                row[k] = None
        out.append(row)
    return upsert(session, DeskUndDaily, out, ["date", "symbol"], chunk=2000)


def und_dates(session: Session, end: date, n: int = 5) -> list[date]:
    q = (
        select(DeskUndDaily.date)
        .where(DeskUndDaily.date <= end)
        .distinct()
        .order_by(DeskUndDaily.date.desc())
        .limit(n)
    )
    return list(session.execute(q).scalars().all())


def load_und(session: Session, day: date) -> dict[str, DeskUndDaily]:
    q = select(DeskUndDaily).where(DeskUndDaily.date == day)
    return {r.symbol: r for r in session.execute(q).scalars().all()}


# ── gamma ───────────────────────────────────────────────────────────────
def save_gamma(session: Session, day: date, rows: Iterable[Mapping], st_days: int) -> int:
    out = [
        {
            "date": day,
            "symbol": str(r["symbol"]).upper(),
            "spot": _num(r.get("spot")),
            "st_gex": _num(r.get("st_gex")),
            "total_gex": _num(r.get("total_gex")),
            "st_days": st_days,
        }
        for r in rows
        if r.get("symbol") and len(str(r["symbol"])) <= 16
    ]
    return upsert(session, DeskGammaDaily, out, ["date", "symbol"], chunk=2000)


def load_gamma(session: Session, day: date) -> dict[str, DeskGammaDaily]:
    q = select(DeskGammaDaily).where(DeskGammaDaily.date == day)
    return {r.symbol: r for r in session.execute(q).scalars().all()}


# ── names / profiles ────────────────────────────────────────────────────
def save_names(session: Session, rows: Sequence[Mapping]) -> int:
    return upsert(session, DeskName, rows, ["symbol"], chunk=2000)


def load_names(session: Session) -> dict[str, dict]:
    """desk_names, filled in from equity_profiles where FMP's screener had no row."""
    out: dict[str, dict] = {}
    for p in session.execute(select(EquityProfile)).scalars().all():
        out[p.symbol] = {
            "name": p.name,
            "sector": p.sector,
            "industry": p.industry,
            "is_etf": p.is_etf,
            "avg_volume": None,
            "price": None,
        }
    for n in session.execute(select(DeskName)).scalars().all():
        base = out.get(n.symbol, {})
        out[n.symbol] = {
            "name": n.name or base.get("name"),
            "sector": n.sector or base.get("sector"),
            "industry": n.industry or base.get("industry"),
            "is_etf": n.is_etf if n.is_etf is not None else base.get("is_etf"),
            "avg_volume": n.avg_volume,
            "price": n.price,
            "market_cap": n.market_cap,
            "beta": n.beta,
            "updated": n.updated,
        }
    return out


def names_updated(session: Session) -> date | None:
    return session.execute(select(func.max(DeskName.updated))).scalar()


# ── bars (equity_bars for the S&P 1500 + ETFs, desk_bars for the rest) ─────
def equity_symbols(session: Session) -> set[str]:
    return set(session.execute(select(EquityBar.symbol).distinct()).scalars().all())


def save_bars(session: Session, rows: Sequence[Mapping]) -> int:
    rows = [
        {k: r.get(k) for k in ("symbol", "date", "open", "high", "low", "close", "volume")}
        for r in rows
    ]
    return upsert(session, DeskBar, rows, ["symbol", "date"], chunk=2000)


def last_desk_bar_dates(session: Session) -> dict[str, date]:
    q = select(DeskBar.symbol, func.max(DeskBar.date)).group_by(DeskBar.symbol)
    return {s: d for s, d in session.execute(q).all()}


def load_bars(
    session: Session, symbols: Iterable[str], start: date, end: date | None = None
) -> dict[str, list[tuple]]:
    """{sym: [(date, open, high, low, close, volume)] oldest first} from both bar tables."""
    syms = list(dict.fromkeys(symbols))
    out: dict[str, dict[date, tuple]] = defaultdict(dict)
    for model in (DeskBar, EquityBar):  # equity_bars wins on overlap (loaded last)
        for i in range(0, len(syms), 2000):
            q = select(
                model.symbol,
                model.date,
                model.open,
                model.high,
                model.low,
                model.close,
                model.volume,
            ).where(model.symbol.in_(syms[i : i + 2000]), model.date >= start)
            if end is not None:
                q = q.where(model.date <= end)
            for sym, d, o, h, lo, c, v in session.execute(q).all():
                out[sym][d] = (d, o, h, lo, c, v)
    return {s: [m[d] for d in sorted(m)] for s, m in out.items()}


# ── picks + briefs ──────────────────────────────────────────────────────
def save_picks(
    session: Session, day: date, source: str, picks: Sequence[tuple[str, str, Mapping | None]]
) -> int:
    """Replace the (day, source) pick set: [(symbol, role, info)]. Idempotent."""
    session.execute(delete(DeskPick).where(DeskPick.date == day, DeskPick.source == source))
    rows = [
        {"date": day, "source": source, "symbol": s.upper(), "role": role, "info": dict(info or {})}
        for s, role, info in picks
        if s
    ]
    rows = list({(r["symbol"], r["role"]): r for r in rows}.values())
    n = upsert(session, DeskPick, rows, ["date", "source", "symbol", "role"], commit=False)
    session.commit()
    return n


def load_picks(session: Session, source: str | None = None) -> list[DeskPick]:
    q = select(DeskPick)
    if source:
        q = q.where(DeskPick.source == source)
    return list(session.execute(q.order_by(DeskPick.date)).scalars().all())


def save_brief(session: Session, day: date, payload: Mapping) -> None:
    row = {
        "date": day,
        "n_screen": len(payload.get("screen") or []),
        "top_pick": (payload.get("top_pick") or {}).get("symbol"),
        "payload": dict(payload),
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None),  # noqa: UP017
    }
    upsert(session, DeskBriefDaily, [row], ["date"])


def load_brief(session: Session, day: date | None = None) -> DeskBriefDaily | None:
    q = select(DeskBriefDaily)
    q = q.where(DeskBriefDaily.date == day) if day else q.order_by(DeskBriefDaily.date.desc())
    return session.execute(q.limit(1)).scalars().first()
