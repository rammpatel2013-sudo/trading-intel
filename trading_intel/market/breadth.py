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


# ── Bull/Bear Line + cumulative A-D line + McClellan + divergence ──────────────
# The regime half of breadth (Norseman method + the synthesis engine). All pure
# (numbers-in → numbers-out) and unit-tested; the collector banks a daily
# ``breadth_snapshots`` row so the A-D line and the divergence-duration read
# (which need history) can accumulate. Descriptor only (rule 4).

def weekly_last_closes(dated_closes: Sequence[tuple]) -> list[float]:
    """Collapse ``(date, close)`` daily rows to ONE last close per ISO week.

    Oldest→newest. Skips ``None`` closes. Used to feed ``bull_bear_line`` (Norseman
    ratchets on the *weekly* close).
    """
    by_week: dict[tuple[int, int], tuple] = {}
    for d, c in dated_closes:
        if c is None or d is None:
            continue
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        prev = by_week.get(key)
        if prev is None or d >= prev[0]:
            by_week[key] = (d, float(c))
    return [by_week[k][1] for k in sorted(by_week)]


def bull_bear_line(weekly_closes: Sequence[float], *, pct: float = 0.10) -> float | None:
    """Norseman Bull/Bear Line = ``(1 − pct)`` × the highest weekly close TO DATE.

    Ratchets UP with every new weekly-closing high, never down (it's just the
    running max × 0.90). ``None`` if there is no history.
    """
    highs = [float(c) for c in weekly_closes if c is not None]
    return (1.0 - pct) * max(highs) if highs else None


def ad_line_next(prev_ad_line: float | None, advancers: int, decliners: int) -> int:
    """Cumulative Advance-Decline line: prior level + today's net (adv − decl)."""
    base = 0 if prev_ad_line is None else int(prev_ad_line)
    return base + int(advancers) - int(decliners)


def _ema(series: Sequence[float], span: int) -> float | None:
    vals = [float(x) for x in series if x is not None]
    if not vals:
        return None
    k = 2.0 / (span + 1.0)
    e = vals[0]
    for x in vals[1:]:
        e = x * k + e * (1.0 - k)
    return e


def mcclellan(
    net_adv_series: Sequence[float], prev_summation: float | None = None
) -> tuple[float | None, float | None]:
    """McClellan Oscillator (EMA19 − EMA39 of daily net advances) + Summation Index.

    ``net_adv_series`` = oldest→newest daily (advancers − decliners). Summation =
    ``prev_summation`` + today's oscillator (seeded from today's oscillator when no
    prior is supplied). Returns ``(oscillator, summation)`` — either may be ``None``.
    """
    if len([x for x in net_adv_series if x is not None]) < 2:
        return None, prev_summation
    e19 = _ema(net_adv_series, 19)
    e39 = _ema(net_adv_series, 39)
    if e19 is None or e39 is None:
        return None, prev_summation
    osc = e19 - e39
    summ = osc if prev_summation is None else float(prev_summation) + osc
    return osc, summ


def breadth_divergence(
    price_series: Sequence[float], ad_series: Sequence[float], *, lookback: int = 12
) -> dict:
    """Cumulative A-D line vs price over ``lookback`` points → divergence read.

    Returns ``{state, length, detail}`` with ``state`` ∈ {confirming, bearish_div,
    bullish_div, none}. ``bearish_div`` = price at a new window high while the A-D
    line is NOT (breadth lagging — the classic top-warning "gap", Norseman's read);
    ``bullish_div`` = the mirror at lows; ``confirming`` = both make the high.
    ``length`` = trailing run of sessions the divergence has held (its duration).
    """
    p = [float(x) for x in price_series if x is not None]
    a = [float(x) for x in ad_series if x is not None]
    n = min(len(p), len(a))
    if n < 3:
        return {"state": "none", "length": 0, "detail": "building"}
    p = p[-min(lookback, n):]
    a = a[-min(lookback, n):]
    price_high = p[-1] >= max(p)
    ad_high = a[-1] >= max(a)
    price_low = p[-1] <= min(p)
    ad_low = a[-1] <= min(a)
    if price_high and ad_high:
        state = "confirming"
    elif price_high and not ad_high:
        state = "bearish_div"
    elif price_low and not ad_low:
        state = "bullish_div"
    else:
        state = "none"

    length = 0
    m = len(p)
    if state == "bearish_div":
        for i in range(m - 1, 0, -1):
            if p[i] >= max(p[: i + 1]) and a[i] < max(a[: i + 1]):
                length += 1
            else:
                break
    elif state == "bullish_div":
        for i in range(m - 1, 0, -1):
            if p[i] <= min(p[: i + 1]) and a[i] > min(a[: i + 1]):
                length += 1
            else:
                break
    detail = {
        "confirming": "breadth confirms price (no gap)",
        "bearish_div": "price up, breadth lagging — top-warning gap",
        "bullish_div": "price down, breadth firmer — washout",
        "none": "no clear divergence",
    }[state]
    return {"state": state, "length": length, "detail": detail}
