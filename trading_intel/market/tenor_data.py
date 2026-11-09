"""DB loaders + builders for the tenor gamma / vega view (created 2026-11-09).

Reads one ``oi_chain_eod`` book (the same rows ``get_profile`` prices: every
``convex%`` source on that ``ts``, so the AM-settled monthlies stored as
``convex_am_set`` are included) and the spot ``get_profile`` uses (latest
``greeks_snapshots`` spot on/before the book day). Pure maths lives in
``greeks.tenor_buckets``. History is a JSON file (no new table):
``reports/tenor/history_<SYM>.json`` - one row per book day, idempotent upsert.

Descriptor only (rule 4): positioning read, not a trade signal.
"""

# ruff: noqa: ANN401  (duck-typed DB values)


from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.greeks.tenor_buckets import (
    BUCKET_KEYS,
    DISCLAIMER,
    GAMMA_UNITS,
    VEGA_UNITS,
    tenor_gamma,
    tenor_vega,
)
from trading_intel.memory.models import GreeksSnapshot, OiChainEod

REPO = Path(__file__).resolve().parents[2]
HIST_DIR = REPO / "reports" / "tenor"
DATA_DIR = REPO / "scripts" / "cvxmap" / "data"


def hist_path(symbol: str, root: Path | None = None) -> Path:
    return (root or HIST_DIR) / f"history_{symbol.upper()}.json"


def book_days(session: Session, symbol: str) -> list[date]:
    """Every distinct book day still stored for ``symbol`` (oldest first)."""
    rows = (
        session.execute(
            select(func.distinct(OiChainEod.ts))
            .where(OiChainEod.symbol == symbol.upper(), OiChainEod.source.like("convex%"))
            .order_by(OiChainEod.ts)
        )
        .scalars()
        .all()
    )
    return sorted({_as_day(r) for r in rows})


def _as_day(x: Any) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.fromisoformat(str(x)).date()  # SQLite DISTINCT hands back text


def latest_book_ts(session: Session, symbol: str, as_of: date | None = None) -> datetime | None:
    q = select(OiChainEod.ts).where(
        OiChainEod.symbol == symbol.upper(), OiChainEod.source.like("convex%")
    )
    if as_of is not None:
        q = q.where(OiChainEod.ts < datetime.combine(as_of + timedelta(days=1), time.min))
    return session.execute(q.order_by(OiChainEod.ts.desc()).limit(1)).scalar_one_or_none()


def spot_on(session: Session, symbol: str, day: date) -> float | None:
    """Latest aggregate spot on/before ``day`` (same source as get_profile)."""
    row = session.execute(
        select(GreeksSnapshot.spot)
        .where(
            GreeksSnapshot.symbol == symbol.upper(),
            GreeksSnapshot.source == "convex",
            GreeksSnapshot.ts < datetime.combine(day + timedelta(days=1), time.min),
            GreeksSnapshot.spot.isnot(None),
        )
        .order_by(GreeksSnapshot.ts.desc())
        .limit(1)
    ).first()
    return float(row[0]) if row and row[0] else None


def load_book(session: Session, symbol: str, ts: datetime) -> pd.DataFrame:
    rows = session.execute(
        select(
            OiChainEod.strike, OiChainEod.cp, OiChainEod.iv, OiChainEod.oi, OiChainEod.expiry
        ).where(
            OiChainEod.symbol == symbol.upper(),
            OiChainEod.source.like("convex%"),
            OiChainEod.ts == ts,
            OiChainEod.iv.isnot(None),
            OiChainEod.oi > 0,
        )
    ).all()
    return pd.DataFrame(rows, columns=["strike", "cp", "iv", "oi", "expiry"])


def compute_day(
    session: Session,
    symbol: str = "SPX",
    day: date | None = None,
    *,
    span: float = 0.12,
    step: float = 25.0,
) -> dict[str, Any]:
    """Gamma + vega by tenor for the newest book on/before ``day`` (None = latest)."""
    sym = symbol.upper()
    ts = latest_book_ts(session, sym, day)
    if ts is None:
        return {"symbol": sym, "found": False, "reason": "no stored chain book"}
    book_day = ts.date() if isinstance(ts, datetime) else ts
    spot = spot_on(session, sym, book_day)
    if spot is None:
        return {"symbol": sym, "as_of": book_day.isoformat(), "found": False, "reason": "no spot"}
    chain = load_book(session, sym, ts)
    g = tenor_gamma(chain, spot, book_day, span=span, step=step)
    v = tenor_vega(chain, spot, book_day)
    if not g:
        return {
            "symbol": sym,
            "as_of": book_day.isoformat(),
            "found": False,
            "reason": "empty book",
        }
    return {
        "symbol": sym,
        "as_of": book_day.isoformat(),
        "found": True,
        "spot": spot,
        "gamma": g,
        "vega": v,
        "note": DISCLAIMER,
    }


def history_row(day_obj: dict[str, Any]) -> dict[str, Any]:
    """Compact one-day history row from a ``compute_day`` result."""
    g, v = day_obj["gamma"], day_obj.get("vega") or {}
    row: dict[str, Any] = {
        "date": day_obj["as_of"],
        "spot": round(day_obj["spot"], 2),
        "total": g["total"]["at_spot"],
        "zero_cross": g["total"]["zero_cross"],
        "vega_total": (v.get("total") or {}).get("naive"),
    }
    for k in BUCKET_KEYS:
        b = g["buckets"][k]
        row[f"g_{k}"] = b["at_spot"]
        row[f"z_{k}"] = b["zero_cross"]
        row[f"v_{k}"] = ((v.get("buckets") or {}).get(k) or {}).get("naive")
    return row


def read_history(symbol: str, root: Path | None = None) -> list[dict[str, Any]]:
    p = hist_path(symbol, root)
    try:
        rows = json.loads(p.read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        return []
    return sorted(rows, key=lambda r: r["date"])


def write_history(symbol: str, rows: list[dict[str, Any]], root: Path | None = None) -> Path:
    """Upsert ``rows`` by date into the history file (idempotent; atomic replace)."""
    p = hist_path(symbol, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    have = {r["date"]: r for r in read_history(symbol, root)}
    for r in rows:
        have[r["date"]] = r
    obj = {
        "symbol": symbol.upper(),
        "units": {"gamma": GAMMA_UNITS, "vega": VEGA_UNITS},
        "note": DISCLAIMER,
        "rows": [have[d] for d in sorted(have)],
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(p)
    return p


def backfill(
    session: Session,
    symbol: str = "SPX",
    root: Path | None = None,
    *,
    step: float = 25.0,
    write: bool = True,
) -> list[dict[str, Any]]:
    """Rebuild the history for every book day still in ``oi_chain_eod`` (write=False: no file)."""
    rows = []
    for d in book_days(session, symbol):
        obj = compute_day(session, symbol, d, step=step)
        if obj.get("found") and obj["as_of"] == d.isoformat():
            rows.append(history_row(obj))
    if rows and write:
        write_history(symbol, rows, root)
    return rows


def change_vs(rows: list[dict[str, Any]], sessions_back: int = 5) -> dict[str, Any] | None:
    """Bucket-by-bucket change of gamma at spot vs ``sessions_back`` stored days earlier."""
    if len(rows) <= sessions_back:
        return None
    a, b = rows[-1 - sessions_back], rows[-1]
    out = {"from": a["date"], "to": b["date"], "total": _d(b["total"], a["total"])}
    for k in BUCKET_KEYS:
        out[k] = _d(b.get(f"g_{k}"), a.get(f"g_{k}"))
    return out


def _d(x: float | None, y: float | None) -> float | None:
    return None if x is None or y is None else round(x - y, 4)
