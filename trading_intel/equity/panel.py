"""Dense date x symbol arrays for the equity layers (numpy only - no pandas in the job path).

``Panel`` holds prices, volume, market cap, index membership and point-in-time fundamentals
(trailing-twelve-month sums, forward-filled from the day each quarter became public).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from trading_intel.equity.universe import Interval

FUND_FIELDS = (
    "ttm_revenue",
    "ttm_gross_profit",
    "ttm_net_income",
    "ttm_ocf",
    "book_equity",
    "total_assets",
    "total_debt",
    "rev_growth",
)


@dataclass
class Panel:
    dates: list[date]
    symbols: list[str]
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    volume: np.ndarray
    mcap: np.ndarray
    member: np.ndarray  # bool: in the universe that day
    sector: list[str | None]
    fund: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def returns(self) -> np.ndarray:
        r = np.full_like(self.close, np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            r[1:] = self.close[1:] / self.close[:-1] - 1.0
        return r

    def col(self, symbol: str) -> int | None:
        try:
            return self.symbols.index(symbol)
        except ValueError:
            return None


def build_panel(
    bars: Sequence[tuple],
    intervals: Sequence[Interval],
    profiles: Mapping[str, object],
    funds: Mapping[str, Sequence[Mapping]] | None = None,
    *,
    codes: Iterable[str] = ("SP500", "SP400", "SP600"),
    extra_symbols: Iterable[str] = (),
) -> Panel:
    """From in-memory bars rows = (symbol, date, open, high, low, close, volume, market_cap)."""
    keep = _universe(intervals, codes) | set(extra_symbols)
    rows = [b for b in bars if b[0] in keep]
    dates = sorted({b[1] for b in rows})
    symbols = sorted({b[0] for b in rows})
    return _assemble(dates, symbols, rows, intervals, profiles, funds, codes)


def load_panel(
    session: object,
    start: date,
    intervals: Sequence[Interval],
    profiles: Mapping[str, object],
    funds: Mapping[str, Sequence[Mapping]] | None = None,
    *,
    codes: Iterable[str] = ("SP500", "SP400", "SP600"),
    extra_symbols: Iterable[str] = (),
) -> Panel:
    """Stream ``equity_bars`` straight into arrays (no 1M-tuple list on the 3.8 GB NAS)."""
    from sqlalchemy import select

    from trading_intel.memory.models_equity import EquityBar

    keep = _universe(intervals, codes) | set(extra_symbols)
    dates = [
        d
        for (d,) in session.execute(  # type: ignore[attr-defined]
            select(EquityBar.date)
            .where(EquityBar.date >= start)
            .distinct()
            .order_by(EquityBar.date)
        )
    ]
    have = {
        s
        for (s,) in session.execute(  # type: ignore[attr-defined]
            select(EquityBar.symbol).where(EquityBar.date >= start).distinct()
        )
    }
    symbols = sorted(keep & have)
    q = (
        select(
            EquityBar.symbol,
            EquityBar.date,
            EquityBar.open,
            EquityBar.high,
            EquityBar.low,
            EquityBar.close,
            EquityBar.volume,
            EquityBar.market_cap,
        )
        .where(EquityBar.date >= start)
        .execution_options(yield_per=20000)
    )
    want = set(symbols)
    rows = (r for r in session.execute(q) if r[0] in want)  # type: ignore[attr-defined]
    return _assemble(dates, symbols, rows, intervals, profiles, funds, codes)


def _universe(intervals: Sequence[Interval], codes: Iterable[str]) -> set[str]:
    want = set(codes)
    return {iv.symbol for iv in intervals if iv.index_code in want}


def _assemble(
    dates: Sequence[date],
    symbols: Sequence[str],
    rows: Iterable[tuple],
    intervals: Sequence[Interval],
    profiles: Mapping[str, object],
    funds: Mapping[str, Sequence[Mapping]] | None,
    codes: Iterable[str],
) -> Panel:
    want = set(codes)
    di = {d: i for i, d in enumerate(dates)}
    si = {s: i for i, s in enumerate(symbols)}
    shape = (len(dates), len(symbols))
    close, high, low, vol, mcap = (np.full(shape, np.nan) for _ in range(5))
    for s, d, _o, h, lo, c, v, mc in rows:
        t, i = di.get(d), si.get(s)
        if t is None or i is None:
            continue
        close[t, i] = c
        high[t, i] = h if h is not None else c
        low[t, i] = lo if lo is not None else c
        vol[t, i] = v if v is not None else np.nan
        mcap[t, i] = mc if mc is not None else np.nan
    member = np.zeros(shape, dtype=bool)
    darr = np.array(list(dates), dtype="datetime64[D]")
    for iv in intervals:
        i = si.get(iv.symbol)
        if i is None or iv.index_code not in want:
            continue
        lo_ok = darr >= np.datetime64(iv.start_date) if iv.start_date else np.ones(len(dates), bool)
        hi_ok = darr < np.datetime64(iv.end_date) if iv.end_date else np.ones(len(dates), bool)
        member[:, i] |= lo_ok & hi_ok
    member &= ~np.isnan(close)
    sector = [
        getattr(profiles.get(s), "sector", None) if profiles.get(s) is not None else None
        for s in symbols
    ]
    p = Panel(list(dates), list(symbols), close, high, low, vol, mcap, member, sector)
    if funds:
        p.fund = fundamental_panels(funds, p.dates, p.symbols)
    return p


def _ttm(quarters: Sequence[Mapping], k: int, key: str) -> float | None:
    if k < 3:
        return None
    span = quarters[k]["period_end"] - quarters[k - 3]["period_end"]
    if span > timedelta(days=300):  # a missing quarter in between - no TTM
        return None
    vals = [quarters[j].get(key) for j in range(k - 3, k + 1)]
    return float(sum(vals)) if all(v is not None for v in vals) else None


def fundamental_points(quarters: Sequence[Mapping]) -> list[tuple[date, dict]]:
    """Per quarter: (available_date, {FUND_FIELDS...}) - pure, for tests."""
    qs = sorted(quarters, key=lambda q: q["period_end"])
    pts = []
    for k, q in enumerate(qs):
        ttm_rev = _ttm(qs, k, "revenue")
        prev = _ttm(qs, k - 4, "revenue") if k >= 7 else None
        vals = {
            "ttm_revenue": ttm_rev,
            "ttm_gross_profit": _ttm(qs, k, "gross_profit"),
            "ttm_net_income": _ttm(qs, k, "net_income"),
            "ttm_ocf": _ttm(qs, k, "operating_cash_flow"),
            "book_equity": q.get("total_equity"),
            "total_assets": q.get("total_assets"),
            "total_debt": q.get("total_debt"),
            "rev_growth": (ttm_rev / prev - 1.0) if (ttm_rev is not None and prev) else None,
        }
        if q.get("available_date"):
            pts.append((q["available_date"], vals))
    return pts


def fundamental_panels(
    funds: Mapping[str, Sequence[Mapping]], dates: Sequence[date], symbols: Sequence[str]
) -> dict[str, np.ndarray]:
    out = {f: np.full((len(dates), len(symbols)), np.nan) for f in FUND_FIELDS}
    darr = np.array(dates, dtype="datetime64[D]")
    for i, s in enumerate(symbols):
        pts = fundamental_points(funds.get(s, []))
        if not pts:
            continue
        pts.sort(key=lambda p: p[0])
        avail = np.array([p[0] for p in pts], dtype="datetime64[D]")
        idx = np.searchsorted(avail, darr, side="right") - 1  # latest public quarter per day
        ok = idx >= 0
        for f in FUND_FIELDS:
            vals = np.array([np.nan if p[1][f] is None else p[1][f] for p in pts], dtype=float)
            col = np.full(len(dates), np.nan)
            col[ok] = vals[idx[ok]]
            out[f][:, i] = col
    return out
