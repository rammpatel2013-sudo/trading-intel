"""S&P-wide market breadth: % of names above their 50/200-day MA, A/D, new highs–lows,
and the 5-session trend of the 50-day figure.

The compute layer is pure (closes-in → numbers-out) and unit-tested. The fetch layer
pulls constituent EOD closes through the CVForge FMP passthrough (existing vendor —
no new vendor, rule 1) best-effort, degrading to ``{}`` so the brief renders a
"computing" breadth block rather than crashing when the feed is slow or quota-limited.
Descriptive context only (FlashAlpha rule 4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Breadth:
    """A day's breadth snapshot plus the 50-day trend over the last few sessions."""

    pct_above_50: int | None
    pct_above_200: int | None
    advancers: int
    decliners: int
    new_highs: int
    new_lows: int
    trend_50: list[int | None]  # oldest → newest, % above 50-day per session
    n: int


def _sma(vals: Sequence[float], window: int) -> float | None:
    w = vals[-window:]
    return sum(w) / len(w) if w else None


def above_ma(closes: Sequence[float], window: int) -> bool | None:
    """Is the latest close above its ``window``-day SMA? ``None`` if too little history."""
    if len(closes) < max(2, window // 2):
        return None
    sma = _sma(closes, window)
    return None if sma is None else closes[-1] > sma


def _pct_above(
    closes_by_symbol: Mapping[str, Sequence[float]], window: int, drop: int = 0
) -> int | None:
    above = total = 0
    for closes in closes_by_symbol.values():
        series = closes[: len(closes) - drop] if drop else closes
        r = above_ma(series, window)
        if r is not None:
            total += 1
            above += 1 if r else 0
    return round(100 * above / total) if total else None


def advance_decline(closes_by_symbol: Mapping[str, Sequence[float]]) -> tuple[int, int]:
    adv = decl = 0
    for closes in closes_by_symbol.values():
        if len(closes) >= 2:
            if closes[-1] > closes[-2]:
                adv += 1
            elif closes[-1] < closes[-2]:
                decl += 1
    return adv, decl


def new_highs_lows(
    closes_by_symbol: Mapping[str, Sequence[float]], *, lookback: int = 252
) -> tuple[int, int]:
    hi = lo = 0
    for closes in closes_by_symbol.values():
        window = closes[-lookback:]
        if len(window) >= 20:
            if closes[-1] >= max(window):
                hi += 1
            elif closes[-1] <= min(window):
                lo += 1
    return hi, lo


def compute_breadth(
    closes_by_symbol: Mapping[str, Sequence[float]], *, sessions: int = 5
) -> Breadth:
    """Full breadth snapshot + a ``sessions``-long trend of the % above the 50-day MA."""
    trend = [_pct_above(closes_by_symbol, 50, drop=o) for o in range(sessions - 1, -1, -1)]
    adv, decl = advance_decline(closes_by_symbol)
    hi, lo = new_highs_lows(closes_by_symbol)
    return Breadth(
        pct_above_50=_pct_above(closes_by_symbol, 50),
        pct_above_200=_pct_above(closes_by_symbol, 200),
        advancers=adv,
        decliners=decl,
        new_highs=hi,
        new_lows=lo,
        trend_50=trend,
        n=len(closes_by_symbol),
    )


# ── fetch (best-effort; NAS-validated) ─────────────────────────────────────────
def sp500_symbols(client: object, *, limit: int = 503) -> list[str]:
    """S&P 500 constituents via the FMP passthrough; ``[]`` on any failure."""
    try:
        data = client.fmp("sp500-constituent")  # type: ignore[attr-defined]
    except Exception:
        return []
    syms = (
        [d["symbol"] for d in data if isinstance(d, dict) and d.get("symbol")]
        if isinstance(data, list)
        else []
    )
    return syms[:limit]


def fetch_closes(
    client: object, symbols: Sequence[str], *, days: int = 320
) -> dict[str, list[float]]:
    """EOD close series per symbol via the FMP light history endpoint (best-effort).

    Skips any symbol the vendor can't serve so one gap never zeros the whole breadth
    read. ``days`` gives enough history for a 200-day MA plus the 5-session trend.
    """
    out: dict[str, list[float]] = {}
    for sym in symbols:
        try:
            data = client.fmp("historical-price-eod/light", {"symbol": sym})  # type: ignore[attr-defined]
        except Exception:  # noqa: S112 — best-effort per symbol; logging 500 misses is noise
            continue
        rows = data if isinstance(data, list) else []
        closes = [
            float(r["price"]) for r in rows if isinstance(r, dict) and r.get("price") is not None
        ]
        if len(closes) >= 60:
            out[sym] = list(reversed(closes[:days]))  # FMP returns newest-first → oldest→newest
    return out
