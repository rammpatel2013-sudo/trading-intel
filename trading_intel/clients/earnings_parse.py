"""Pure parser: ConvexValue ``earn_cal`` raw JSON -> ``EarningsDate`` value objects.

Split out from ``convex_app.py`` so it is unit-testable without httpx and keeps the
HTTP boundary thin (rule 1). Handles ConvexValue's ``{"data": [header, rows]}``
shape (``rows`` is itself a list-of-lists) plus a flat ``[header, row, ...]`` and a
list-of-dicts fallback.

CONFIRMED against live ``earn_cal`` 2026-07-18: header =
``[date, symbol, eps, eps_estimated, time, revenue, revenue_estimated,
fiscal_date_ending, updated_from_date]`` and rows are nested inside ``data[1]``.
Descriptive data only (FlashAlpha rule 4).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone

from trading_intel.clients import EarningsDate

_EPOCH = date(1970, 1, 1)


def _to_date(v: object) -> date | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
        if iv > 1_000_000:
            try:
                return datetime.fromtimestamp(iv, tz=timezone.utc).date()
            except (ValueError, OSError, OverflowError):
                return None
        if iv > 1000:
            return _EPOCH + timedelta(days=iv)
        return None
    s = str(v).strip()
    if not s:
        return None
    head = s.replace("T", " ").split(" ")[0]
    try:
        return date.fromisoformat(head)
    except ValueError:
        pass
    if len(head) == 8 and head.isdigit():
        try:
            return date(int(head[:4]), int(head[4:6]), int(head[6:8]))
        except ValueError:
            return None
    return None


def _norm_session(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).lower()
    if any(k in s for k in ("bmo", "before", "pre-market", "premarket")):
        return "BMO"
    if any(k in s for k in ("amc", "after", "post-market", "postmarket", "aftermarket")):
        return "AMC"
    return None


def _pick(header: list[str], needles: Iterable[str], *, exclude: Iterable[str] = ()) -> int | None:
    for i, h in enumerate(header):
        hl = str(h).lower()
        if any(n in hl for n in needles) and not any(x in hl for x in exclude):
            return i
    return None


def _first(d: dict, keys: Iterable[str]) -> object:
    low = {str(k).lower(): val for k, val in d.items()}
    for k in keys:
        if low.get(k) is not None:
            return low[k]
    return None


def parse_earnings_calendar(raw: object, *, source: str = "convex") -> list[EarningsDate]:
    """Normalize ``earn_cal`` JSON to ``EarningsDate`` rows; ``[]`` when unusable."""
    if not isinstance(raw, dict):
        return []
    data = raw.get("data")
    if not isinstance(data, list) or not data:
        return []
    out: list[EarningsDate] = []

    if isinstance(data[0], dict):
        for d in data:
            if not isinstance(d, dict):
                continue
            sym = _first(d, ("symbol", "ticker", "sym"))
            dt = _to_date(_first(d, ("date", "earnings_date", "report_date", "day", "when")))
            ses = _norm_session(_first(d, ("session", "timing", "time", "hour", "bmo", "amc")))
            if sym and dt:
                out.append(EarningsDate(str(sym).upper(), dt, ses, source))
        return out

    if isinstance(data[0], (list, tuple)):
        header = [str(h) for h in data[0]]
        i_sym = _pick(header, ("sym", "tick"))
        i_dt = _pick(header, ("date",))
        if i_dt is None:
            i_dt = _pick(header, ("day",), exclude=("today",))
        i_ses = _pick(header, ("session", "timing", "bmo", "amc", "hour"))
        if i_ses is None:
            i_ses = _pick(header, ("time",), exclude=("date",))
        # ConvexValue returns {"data": [header, rows]} where ``rows`` is itself a
        # list-of-lists. Also tolerate a flat [header, row, row, ...]. Unwrap.
        body = data[1:]
        if (
            len(body) == 1
            and isinstance(body[0], (list, tuple))
            and body[0]
            and isinstance(body[0][0], (list, tuple))
        ):
            rows = body[0]
        else:
            rows = body
        for r in rows:
            if not isinstance(r, (list, tuple)):
                continue
            sym = r[i_sym] if i_sym is not None and i_sym < len(r) else None
            dt = _to_date(r[i_dt]) if i_dt is not None and i_dt < len(r) else None
            ses = _norm_session(r[i_ses]) if i_ses is not None and i_ses < len(r) else None
            if sym and dt:
                out.append(EarningsDate(str(sym).upper(), dt, ses, source))
        return out

    return []
