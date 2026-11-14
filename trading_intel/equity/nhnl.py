"""NYSE / Nasdaq new highs - new lows from thetrading.tools (ADR-009). Pure parsing + guards.

Guards (2026-11-14 plan): a day whose total issue count is more than 5% under its 20-day median
is ``suspect`` (their pipeline sometimes misses symbols - 149 fetch errors on 11/13) and casts no
vote; a missing day is never back-filled from the day before. Their universe is today's listings
(survivorship) - use today's reading, never backtest on it. The lookback that defines a "new high"
is not published; check one day against another source before leaning on the level.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from statistics import median

_INT_FIELDS = (
    "nyse_new_highs",
    "nyse_new_lows",
    "nyse_issues",
    "nasdaq_new_highs",
    "nasdaq_new_lows",
    "nasdaq_issues",
    "new_highs",
    "new_lows",
    "total_issues",
    "spx_new_highs",
    "spx_new_lows",
    "ndx_new_highs",
    "ndx_new_lows",
)


def _i(x: object) -> int | None:
    try:
        return int(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_rows(raw: Sequence[Mapping], *, since: date | None = None) -> list[dict]:
    out: dict[date, dict] = {}
    for r in raw:
        try:
            d = date.fromisoformat(str(r.get("date"))[:10])
        except ValueError:
            continue
        if since and d < since:
            continue
        out[d] = {"date": d, **{f: _i(r.get(f)) for f in _INT_FIELDS}}
    rows = [out[d] for d in sorted(out)]
    return flag_suspect(rows)


def flag_suspect(rows: list[dict], *, window: int = 20, tol: float = 0.05) -> list[dict]:
    """status = suspect when total issues < (1 - tol) x the median of the previous ``window`` days."""
    hist: list[int] = []
    for r in rows:
        tot = r.get("total_issues")
        ref = median(hist[-window:]) if len(hist) >= 5 else None
        r["status"] = "suspect" if (tot is None or (ref and tot < (1 - tol) * ref)) else "ok"
        if tot is not None:
            hist.append(tot)
    return rows


def net_pct(row: Mapping) -> float | None:
    """(NYSE + Nasdaq new highs - new lows) as % of their issues, one day."""
    nh = (row.get("nyse_new_highs") or 0) + (row.get("nasdaq_new_highs") or 0)
    nl = (row.get("nyse_new_lows") or 0) + (row.get("nasdaq_new_lows") or 0)
    iss = (row.get("nyse_issues") or 0) + (row.get("nasdaq_issues") or 0)
    return (nh - nl) / iss * 100 if iss else None


def ten_day(rows: Sequence[Mapping], as_of: date, *, n: int = 10) -> tuple[float | None, str]:
    """Average daily net % over the last ``n`` ok rows up to ``as_of``; status of ``as_of`` itself.

    Status: ok / suspect / not_collected (no row for ``as_of``).
    """
    today = next((r for r in rows if r["date"] == as_of), None)
    status = "not_collected" if today is None else str(today.get("status") or "ok")
    vals = [net_pct(r) for r in rows if r["date"] <= as_of and r.get("status") == "ok"][-n:]
    vals = [v for v in vals if v is not None]
    if status != "ok" or len(vals) < n // 2:
        return None, status
    return sum(vals) / len(vals), status
