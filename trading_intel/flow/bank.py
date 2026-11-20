"""Flow bank - parse + save the flow data we pay for but did not keep (2026-11-19).

Sources (project doc ``claude/flow-params-catalog-20261119.md``):
- Convex ``und`` screener: every optionable name's option flow after the close (1 call).
- Convex ``flowchart``: 15-second flow series, served for TODAY only (1 call per symbol).
- optionsdata.ai flow records, read from the files ``scripts/od_nightly.py`` already saves.
- FMP 1-minute bars via CVForge for a list of ETFs.
Pure parsing here; ``scheduler/jobs/flow_bank.py`` does the calls. Descriptors only (rule 4).
"""

from __future__ import annotations

import csv
import gzip
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from trading_intel.equity.store import upsert
from trading_intel.memory.models_flow import (
    FLOWCHART_COLS,
    UND_FIELDS,
    Bars1m,
    FlowChart15s,
    FlowUndDaily,
    OdFlowDaily,
)

ET = ZoneInfo("America/New_York")
SPLIT = ("volm_call_buy", "volm_call_sell", "volm_put_buy", "volm_put_sell")


def _num(v: object) -> float | None:
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rows_block(resp: object) -> list:
    """Convex answers ``{data: [[row, ...]]}`` (und) or ``{data: [header, [row, ...]]}``."""
    data = resp.get("data") if isinstance(resp, Mapping) else None
    return data if isinstance(data, list) else []


# ── Convex und ──────────────────────────────────────────────────────────
def parse_und(resp: object, fields: Sequence[str] = UND_FIELDS) -> list[dict]:
    """Rows of a ``und`` call made with params ``["symbol", *fields]`` -> dicts."""
    block = _rows_block(resp)
    rows = block[0] if block and isinstance(block[0], list) else []
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) != len(fields) + 1 or not r[0]:
            continue
        sym = str(r[0]).upper()
        if len(sym) > 16:
            continue
        out.append({"symbol": sym, **{f: _num(v) for f, v in zip(fields, r[1:])}})
    return out


def merge_und(*parts: Sequence[Mapping]) -> list[dict]:
    """Later parts win per symbol, but only when they carry real counters (non-zero value)."""
    out: dict[str, dict] = {}
    for part in parts:
        for r in part:
            if r["symbol"] not in out or (r.get("value") or 0) > 0:
                out[r["symbol"]] = dict(r)
    return list(out.values())


def split_ok(rows: Iterable[Mapping]) -> bool:
    """False when every buy/sell counter is 0 (pulled after the ~18:00 ET reset or on a weekend)."""
    return any((r.get(k) or 0) > 0 for r in rows for k in SPLIT)


def save_und(session: Session, day: date, rows: Sequence[Mapping], captured_at: datetime) -> int:
    out = [{"date": day, "captured_at": captured_at, **r} for r in rows]
    return upsert(session, FlowUndDaily, out, ["date", "symbol"], chunk=1000)


# ── Convex flowchart ────────────────────────────────────────────────────
def parse_flowchart(resp: object, cols: Sequence[str] = FLOWCHART_COLS) -> list[dict]:
    """``{data: [header, [[symbol, epoch_s, *cols], ...]]}`` -> dicts with naive Eastern ``ts``."""
    block = _rows_block(resp)
    if len(block) < 2 or not isinstance(block[1], list):
        return []
    header = [str(h) for h in block[0]] if isinstance(block[0], list) else ["symbol", "time", *cols]
    pos = {h: i for i, h in enumerate(header)}
    if "time" not in pos:
        return []
    out = []
    for r in block[1]:
        if not isinstance(r, list) or len(r) < len(header):
            continue
        t = _num(r[pos["time"]])
        if t is None:
            continue
        ts = datetime.fromtimestamp(t, ET).replace(tzinfo=None, microsecond=0)
        row = {"symbol": str(r[pos.get("symbol", 0)]).upper(), "ts": ts, "trade_date": ts.date()}
        row.update({c: _num(r[pos[c]]) if c in pos else None for c in cols})
        out.append(row)
    seen: dict[tuple, dict] = {}
    for row in out:  # two points can land on the same second after rounding
        seen[(row["symbol"], row["ts"])] = row
    return list(seen.values())


def save_flowchart(session: Session, rows: Sequence[Mapping]) -> int:
    return upsert(session, FlowChart15s, list(rows), ["symbol", "ts"], chunk=2000)


# ── optionsdata.ai flow (from the od_nightly files) ─────────────────────
def _read_gz_csv(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def parse_od_flow(day: date, symbol: str, rows: Iterable[Mapping]) -> list[dict]:
    """Flow records at ``underlying_right`` (expiration NULL) and ``expiration_right`` levels."""
    out: dict[tuple, dict] = {}
    for r in rows:
        if r.get("record") != "flow" or r.get("level") not in (
            "underlying_right",
            "expiration_right",
        ):
            continue
        exp = None
        if r.get("level") == "expiration_right":
            try:
                exp = date.fromisoformat(str(r.get("expiration"))[:10])
            except ValueError:
                continue
        right, cat, side = (str(r.get(k) or "") for k in ("right", "category", "side"))
        if not right or not cat or not side:
            continue
        key = (exp, right, cat, side)
        out[key] = {
            "date": day,
            "symbol": symbol.upper(),
            "expiration": exp,
            "right": right[:4],
            "category": cat[:12],
            "side": side[:10],
            "premium_usd": _num(r.get("premium")),
            "volume": _num(r.get("volume")),
            "count": _num(r.get("count")),
        }
    return list(out.values())


def od_flow_from_dir(day: date, folder: Path) -> list[dict]:
    """Every ``<SYM>_underlying.csv.gz`` + ``<SYM>.csv.gz`` in one od_nightly day folder."""
    out: list[dict] = []
    for und in sorted(folder.glob("*_underlying.csv.gz")):
        sym = und.name[: -len("_underlying.csv.gz")]
        rows = _read_gz_csv(und)
        chain = folder / f"{sym}.csv.gz"
        if chain.exists():
            rows += _read_gz_csv(chain)
        out += parse_od_flow(day, sym, rows)
    return out


def save_od_flow(session: Session, rows: Sequence[Mapping]) -> int:
    keys = ["date", "symbol", "expiration", "right", "category", "side"]
    return upsert(session, OdFlowDaily, list(rows), keys, chunk=2000)


# ── FMP 1-minute bars ───────────────────────────────────────────────────
def parse_fmp_1m(symbol: str, rows: object, source: str = "fmp") -> list[dict]:
    """FMP ``historical-chart/1min`` rows (``date`` = Eastern wall clock) -> bars."""
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, Mapping):
            continue
        try:
            ts = datetime.strptime(str(r.get("date"))[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        out.append(
            {
                "symbol": symbol.upper(),
                "ts": ts,
                "source": source,
                "open": _num(r.get("open")),
                "high": _num(r.get("high")),
                "low": _num(r.get("low")),
                "close": _num(r.get("close")),
                "volume": _num(r.get("volume")),
            }
        )
    return out


def save_bars_1m(session: Session, rows: Sequence[Mapping]) -> int:
    return upsert(session, Bars1m, list(rows), ["symbol", "ts", "source"], chunk=2000)
