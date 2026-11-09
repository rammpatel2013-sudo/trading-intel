"""Confluence layers (b)-(e): OI clusters, VIX-mapped levels, price memory, crowded strikes.

Pure functions over plain dataclasses — no DB, no network. Every layer returns
``(levels, gaps)``: ``levels`` are dicts ``{level, label, strength, kind, ...}`` and
``gaps`` are plain-English notes for anything the inputs could not support (the
report prints them; nothing is ever filled in).

Descriptors only (CLAUDE.md rule 4): each level says where positioning or price
history sits, never which way price goes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from trading_intel.market.confluence_greeks import strike_for_delta

DEFAULT_BETA_PTS = 2.0  # VIX points per 1% SPX drop (15-year check); a parameter


@dataclass(frozen=True, slots=True)
class ChainRow:
    strike: float
    cp: str  # 'C' / 'P'
    dte: int
    iv: float | None = None
    oi: float | None = None
    oi_change: float | None = None
    expiry: date | None = None


@dataclass(frozen=True, slots=True)
class VixOption:
    expiration: date
    strike: float
    kind: str  # 'call' / 'put'
    oi: float | None


@dataclass(frozen=True, slots=True)
class Bar:
    d: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class SmilePoint:
    dte: int  # constant-maturity rung (days)
    delta: float  # |delta| 5..50
    side: str  # 'put' / 'call'
    iv: float
    spot: float | None = None
    near_expiry: date | None = None


@dataclass(frozen=True, slots=True)
class BigTradeLeg:
    trade_date: date
    group: str | None
    strike: float
    cp: str
    expiry: date | None
    notional: float
    financing: bool = False


# ── (b) OI clusters ──────────────────────────────────────────────────────────
def oi_levels(
    chain: Sequence[ChainRow], *, spot: float, top: int = 8, buckets: Sequence[tuple[str, int]] = (
        ("<=45d", 45), ("<=7d", 7)), band: float = 0.12,
) -> tuple[list[dict], list[str]]:
    """Top put-OI and call-OI strikes per DTE bucket (aggregated across expiries).

    Only strikes within ``spot * (1 ± band)`` are ranked (far wings are not levels
    price can reach in the read's horizon). Each level carries the day-over-day
    OI change summed over the same strike/side/bucket.
    """
    gaps: list[str] = []
    if not chain:
        return [], ["no per-strike chain for the day: OI clusters skipped"]
    if all(r.oi_change is None for r in chain):
        gaps.append("chain has no day-over-day OI change column: OI change shown as unknown")
    out: list[dict] = []
    lo, hi = spot * (1 - band), spot * (1 + band)
    for name, dmax in buckets:
        for side in ("P", "C"):
            agg: dict[float, list[float]] = {}
            for r in chain:
                if r.dte > dmax or not r.cp.upper().startswith(side) or not lo <= r.strike <= hi:
                    continue
                a = agg.setdefault(r.strike, [0.0, 0.0, 0.0])
                a[0] += float(r.oi or 0.0)
                if r.oi_change is not None:
                    a[1] += float(r.oi_change)
                    a[2] = 1.0
            ranked = sorted(agg.items(), key=lambda kv: -kv[1][0])[:top]
            if not ranked or ranked[0][1][0] <= 0:
                continue
            vmax = ranked[0][1][0]
            word = "put" if side == "P" else "call"
            for k, (oi, chg, has) in ranked:
                out.append({"level": k, "kind": f"{word}_oi", "bucket": name,
                            "label": f"{word} OI {name} {oi / 1000:,.1f}k",
                            "strength": round(oi / vmax, 3), "oi": oi,
                            "oi_change": chg if has else None})
    return out, gaps


# ── (c) VIX-mapped levels ────────────────────────────────────────────────────
def vix_to_spx(vix_strike: float, vix_spot: float, spot: float, beta_pts: float) -> float:
    """SPX level implied by VIX reaching ``vix_strike``.

    SPX % move = −(VIX_strike − VIX_spot) / beta_pts, where beta_pts is VIX points per
    1% SPX drop. Example: VIX 25.64, strike 30, beta 2.0 → −2.18% → spot × 0.9782.
    """
    pct = -(vix_strike - vix_spot) / beta_pts
    return spot * (1.0 + pct / 100.0)


def measured_beta_pts(
    spx: Sequence[tuple[date, float]], vix: Sequence[tuple[date, float]], *, window: int = 21
) -> float | None:
    """VIX points per 1% SPX drop from a rolling log-return regression (last ``window`` days).

    β = cov(Δln SPX, Δln VIX) / var(Δln SPX); points per 1% drop = −β · VIX_last / 100.
    None when there are fewer than ``window`` aligned returns or the slope is not negative.
    """
    vm = dict(vix)
    pairs = [(d, s, vm[d]) for d, s in sorted(spx) if d in vm and s > 0 and vm[d] and vm[d] > 0]
    if len(pairs) < window + 1:
        return None
    pairs = pairs[-(window + 1):]
    rs = [math.log(pairs[i][1] / pairs[i - 1][1]) for i in range(1, len(pairs))]
    rv = [math.log(pairs[i][2] / pairs[i - 1][2]) for i in range(1, len(pairs))]
    ms, mv = sum(rs) / len(rs), sum(rv) / len(rv)
    var = sum((x - ms) ** 2 for x in rs)
    if var <= 0:
        return None
    beta = sum((x - ms) * (y - mv) for x, y in zip(rs, rv, strict=True)) / var
    pts = -beta * pairs[-1][2] / 100.0
    return pts if pts > 0 else None


def vix_levels(
    options: Sequence[VixOption], *, as_of: date, vix_spot: float | None, spot: float,
    beta_pts: float = DEFAULT_BETA_PTS, beta_measured: float | None = None, top: int = 2,
    n_expiries: int = 2, min_expiry_share: float = 0.10,
) -> tuple[list[dict], list[str]]:
    """Biggest VIX call-OI and put-OI strikes for the next ``n_expiries`` → SPX levels.

    Expiries holding under ``min_expiry_share`` of the biggest expiry's total OI (thin
    weeklies) are skipped and named in the gaps.
    """
    if vix_spot is None or vix_spot <= 0:
        return [], ["no VIX close for the day: VIX-mapped levels skipped"]
    totals: dict[date, float] = {}
    for o in options:
        if o.expiration > as_of:
            totals[o.expiration] = totals.get(o.expiration, 0.0) + float(o.oi or 0.0)
    if not totals or max(totals.values()) <= 0:
        return [], ["no VIX options chain stored for the day: VIX-mapped levels skipped"]
    gaps = [] if beta_measured else [
        "measured spot-vol beta unavailable: only the 2.0 default mapping shown"]
    biggest = max(totals.values())
    exps = []
    for e in sorted(totals):
        if totals[e] < min_expiry_share * biggest:
            share = totals[e] / biggest
            gaps.append(f"VIX expiry {e.isoformat()} skipped: thin ({share:.0%} of the "
                        "biggest expiry's OI)")
            continue
        exps.append(e)
        if len(exps) == n_expiries:
            break
    out: list[dict] = []
    for kind in ("call", "put"):
        sel = [o for o in options if o.expiration in exps and o.kind.lower().startswith(kind[0])
               and (o.oi or 0) > 0]
        # strength is relative to the biggest strike across BOTH expiries, so a thin
        # weekly does not weigh as much as the monthly
        vmax = max((float(o.oi or 0) for o in sel), default=1.0) or 1.0
        for e in exps:
            rows = sorted((o for o in sel if o.expiration == e), key=lambda o: -(o.oi or 0.0))
            if not rows:
                gaps.append(f"no VIX {kind} OI for {e.isoformat()}")
                continue
            for o in rows[:top]:
                lvl = vix_to_spx(o.strike, vix_spot, spot, beta_pts)
                alt = vix_to_spx(o.strike, vix_spot, spot, beta_measured) if beta_measured else None
                out.append({
                    "level": round(lvl, 1), "kind": f"vix_{kind}_oi", "bucket": e.isoformat(),
                    "label": f"VIX {e.strftime('%m/%d')} {o.strike:g} {kind} "
                             f"{float(o.oi or 0) / 1000:,.0f}k",
                    "strength": round(float(o.oi or 0) / vmax, 3), "vix_strike": o.strike,
                    "oi": float(o.oi or 0), "level_measured_beta": round(alt, 1) if alt else None,
                })
    return out, gaps


# ── (d) price memory ─────────────────────────────────────────────────────────
def sma(closes: Sequence[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def swing_pivots(bars: Sequence[Bar], *, k: int = 5, lookback: int = 250) -> list[tuple[str, Bar]]:
    """Pivot highs/lows: bar i is the max high (min low) of bars i-k..i+k."""
    b = list(bars)[-lookback:]
    out: list[tuple[str, Bar]] = []
    for i in range(k, len(b) - k):
        win = b[i - k:i + k + 1]
        if b[i].high >= max(x.high for x in win):
            out.append(("high", b[i]))
        if b[i].low <= min(x.low for x in win):
            out.append(("low", b[i]))
    return out


def anchored_vwap(bars: Sequence[Bar], anchor: date) -> float | None:
    """Volume-weighted typical price (H+L+C)/3 from ``anchor`` through the last bar."""
    num = den = 0.0
    for x in bars:
        if x.d >= anchor and x.volume:
            num += (x.high + x.low + x.close) / 3.0 * float(x.volume)
            den += float(x.volume)
    return num / den if den > 0 else None


def price_levels(
    spx: Sequence[Bar], *, as_of: date, spot: float, spy: Sequence[Bar] = (), band: float = 0.10,
) -> tuple[list[dict], list[str]]:
    """SMAs, 5-bar swing pivots (last 250 bars), prior-month H/L, anchored VWAP via SPY."""
    bars = [x for x in sorted(spx, key=lambda x: x.d) if x.d <= as_of]
    gaps: list[str] = []
    out: list[dict] = []
    if not bars:
        return [], ["no SPX daily bars: price-memory layer skipped"]
    closes = [x.close for x in bars]
    for n in (20, 50, 100, 200):
        v = sma(closes, n)
        if v is None:
            gaps.append(f"SMA{n} needs {n} daily bars, only {len(bars)} stored")
        else:
            out.append({"level": round(v, 1), "kind": "sma", "label": f"SMA{n}", "strength": 1.0})
    if len(bars) < 250:
        gaps.append(f"swing pivots use the last 250 bars; only {len(bars)} stored")
    lo, hi = spot * (1 - band), spot * (1 + band)
    for kind, b in swing_pivots(bars):
        lvl = b.high if kind == "high" else b.low
        if lo <= lvl <= hi:
            out.append({"level": round(lvl, 1), "kind": f"swing_{kind}",
                        "label": f"swing {kind} {b.d.strftime('%m/%d')}", "strength": 0.6})
    first = as_of.replace(day=1)
    prev = [x for x in bars if x.d < first and (x.d.year, x.d.month) == (
        (first.year, first.month - 1) if first.month > 1 else (first.year - 1, 12))]
    if prev:
        out.append({"level": round(min(x.low for x in prev), 1), "kind": "prior_month_low",
                    "label": "prior month low", "strength": 0.8})
        out.append({"level": round(max(x.high for x in prev), 1), "kind": "prior_month_high",
                    "label": "prior month high", "strength": 0.8})
    else:
        gaps.append("no bars for the prior calendar month")
    out += _avwap_levels(bars, spy, as_of=as_of, gaps=gaps)
    return out, gaps


def _avwap_levels(
    bars: list[Bar], spy: Sequence[Bar], *, as_of: date, gaps: list[str]
) -> list[dict]:
    sp = [x for x in sorted(spy, key=lambda x: x.d) if x.d <= as_of]
    if not sp or not any(x.volume for x in sp):
        gaps.append("anchored VWAP omitted: SPX has no volume and no SPY OHLCV is stored")
        return []
    last_spy = {x.d: x.close for x in sp}.get(bars[-1].d)
    if not last_spy:
        gaps.append("anchored VWAP omitted: no SPY bar on the SPX bar's date for the ratio")
        return []
    ratio = bars[-1].close / last_spy
    piv = swing_pivots(bars)
    out = []
    for kind in ("low", "high"):
        cand = [b for k, b in piv if k == kind]
        if not cand:
            continue
        anchor = cand[-1].d
        v = anchored_vwap(sp, anchor)
        if v is not None:
            out.append({"level": round(v * ratio, 1), "kind": f"avwap_swing_{kind}",
                        "label": f"anchored VWAP from swing {kind} {anchor.strftime('%m/%d')}",
                        "strength": 0.8})
    return out


# ── (e) crowded-structure strikes ────────────────────────────────────────────
def _smile_iv(points: Sequence[SmilePoint], rung: int, side: str, delta: float) -> float | None:
    pts = sorted((p.delta, p.iv) for p in points if p.dte == rung and p.side.lower() == side)
    if not pts:
        return None
    for (d0, v0), (d1, v1) in pairwise(pts):
        if d0 <= delta <= d1:
            return v0 + (v1 - v0) * (delta - d0) / (d1 - d0) if d1 > d0 else v0
    return None


def third_friday(y: int, m: int) -> date:
    d = date(y, m, 15)
    return date(y, m, 15 + (4 - d.weekday()) % 7)


def next_monthly_dte(as_of: date, *, min_days: int = 8) -> int:
    """Days to the next standard monthly (third Friday) at least ``min_days`` away."""
    y, m = as_of.year, as_of.month
    for _ in range(3):
        tf = third_friday(y, m)
        if (tf - as_of).days >= min_days:
            return (tf - as_of).days
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return 30


def crowd_levels(
    smile: Sequence[SmilePoint], trades: Sequence[BigTradeLeg], *, as_of: date, spot: float,
    top_trades: int = 6, band: float = 0.15,
) -> tuple[list[dict], list[str]]:
    """22Δ/25Δ put+call strikes (front weekly + next monthly rung) and big-trade strikes."""
    gaps: list[str] = []
    out: list[dict] = []
    rungs = sorted({p.dte for p in smile})
    if not rungs:
        gaps.append("no constant-maturity smile for the day: 22/25-delta strikes skipped")
    else:
        mdte = next_monthly_dte(as_of)
        picks = [("front weekly", min(rungs, key=lambda r: abs(r - 7))),
                 ("next monthly", min(rungs, key=lambda r: abs(r - mdte)))]
        seen = set()
        for name, rung in picks:
            if rung in seen:
                continue
            seen.add(rung)
            sp = next((p.spot for p in smile if p.dte == rung and p.spot), None) or spot
            for side in ("put", "call"):
                for dlt in (22.0, 25.0):
                    iv = _smile_iv(smile, rung, side, dlt)
                    if iv is None:
                        gaps.append(f"{rung}d smile missing {dlt:g}Δ {side}")
                        continue
                    target = -dlt / 100 if side == "put" else dlt / 100
                    k = strike_for_delta(sp, target, iv, rung / 365.0, side[0].upper())
                    if k:
                        out.append({"level": round(k, 1), "kind": f"{int(dlt)}d_{side}",
                                    "bucket": f"{rung}d",
                                    "label": f"{dlt:g}Δ {side} {rung}d ({name})",
                                    "strength": 1.0, "iv": iv})
    out += _trade_levels(trades, spot=spot, top=top_trades, band=band, gaps=gaps)
    return out, gaps


def _trade_levels(trades: Sequence[BigTradeLeg], *, spot: float, top: int, band: float,
                  gaps: list[str]) -> list[dict]:
    if not trades:
        gaps.append("no index big-trade prints stored for the last 3 sessions")
        return []
    groups: dict[str, list[BigTradeLeg]] = {}
    for i, t in enumerate(trades):
        groups.setdefault(t.group or f"_single{i}", []).append(t)
    structs = [(sum(t.notional for t in g), g) for g in groups.values()
               if not any(t.financing for t in g)]
    skipped = len(groups) - len(structs)
    if skipped:
        gaps.append(f"{skipped} financing/box structures excluded from big-trade strikes")
    structs.sort(key=lambda s: -s[0])
    structs = structs[:top]
    if not structs:
        return []
    pmax = structs[0][0] or 1.0
    out = []
    for prem, g in structs:
        legs = " / ".join(sorted({f"{t.strike:g}{t.cp}" for t in g}))
        exp = g[0].expiry.strftime("%m/%d/%y") if g[0].expiry else "?"
        for k in sorted({t.strike for t in g}):
            if spot * (1 - band) <= k <= spot * (1 + band):
                out.append({"level": k, "kind": "big_trade",
                            "label": f"big trade {legs} {exp} ${prem / 1e6:,.0f}M "
                                     f"({g[0].trade_date.strftime('%m/%d')})",
                            "strength": round(prem / pmax, 3), "premium": prem})
    return out
