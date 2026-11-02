"""Rebuild the ``breadth_snapshots`` series from DATED constituent closes. Pure.

Why (2026-10-29): the live job ran at 16:30, before the vendor's bars and our own
SPX bar were in, so most rows since 2026-08-11 carried the PREVIOUS session's
breadth under today's date; its McClellan read the oldest 80 rows instead of the
newest; and weekend runs before the session gate walked the A-D line down. Rather
than patch row by row, the series is recomputed by date from one history pull.

Every per-day number reuses the live job's own functions (``market.breadth``) on
the same 320-bar window, so a rebuilt row equals what a correctly timed live run
would have banked. Survivorship caveat (same as the A-D backfill): today's
membership is applied to every past day — trust the shape, not the absolute level.
Descriptor only (rule 4).
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import date

from trading_intel.market import breadth as bm

WINDOW = 320  # bars per symbol the live job keeps (fetch_closes days=320)
HIST = 80  # rows of context the live job feeds McClellan / divergence


def per_day_counts(
    dated: Mapping[str, Mapping[date, float]],
    days: Sequence[date],
    *,
    window: int = WINDOW,
    full: bool = True,
) -> dict[date, dict]:
    """Per-session breadth counts from ``{symbol: {date: close}}``.

    A symbol counts on ``d`` only if it has a bar ON ``d`` (no stale carry-forward).
    ``full=False`` returns advancers/decliners only (the S&P 600 rail).
    """
    series = {}
    for sym, by_day in dated.items():
        ds = sorted(by_day)
        series[sym] = (ds, [float(by_day[d]) for d in ds])
    out: dict[date, dict] = {}
    for d in days:
        cbs: dict[str, list[float]] = {}
        for sym, (ds, cs) in series.items():
            i = bisect_right(ds, d)
            if i == 0 or ds[i - 1] != d:
                continue
            cbs[sym] = cs[max(0, i - window):i]
        if not cbs:
            continue
        adv, dec = bm.advance_decline(cbs)
        row = {"advancers": adv, "decliners": dec, "n_constituents": len(cbs)}
        if full:
            hi, lo = bm.new_highs_lows(cbs)
            row.update(
                new_highs=hi,
                new_lows=lo,
                pct_above_50=bm._pct_above(cbs, 50),
                pct_above_200=bm._pct_above(cbs, 200),
            )
        out[d] = row
    return out


def assemble(
    counts: Mapping[date, dict],
    spx_bars: Sequence[tuple[date, float, float]] = (),
    *,
    full: bool = True,
) -> list[dict]:
    """Chain the per-day counts into banked rows, oldest → newest.

    ``spx_bars`` = ``(date, intraday high, close)`` over ALL available history (the
    Bull/Bear Line ratchets on the all-time high). The A-D line starts at 0 on the
    first day. McClellan and divergence use the newest ``HIST`` rows before each day
    plus that day — the corrected live semantics.
    """
    bars = sorted(spx_bars)
    rows: list[dict] = []
    ad = 0
    summ: float | None = None
    net_hist: list[float] = []
    px_hist: list[tuple[float, float]] = []
    bi, run_high = 0, None
    for d in sorted(counts):
        c = counts[d]
        net = int(c["advancers"]) - int(c["decliners"])
        ad += net
        row = {"ts": d, **c, "net_adv": net, "ad_line": ad}
        if full:
            close = None
            while bi < len(bars) and bars[bi][0] <= d:
                run_high = bars[bi][1] if run_high is None else max(run_high, bars[bi][1])
                if bars[bi][0] == d:
                    close = bars[bi][2]
                bi += 1
            bbl = 0.9 * run_high if run_high is not None else None
            osc, summ = bm.mcclellan(net_hist[-HIST:] + [net], summ)
            p = [x for x, _ in px_hist[-HIST:]]
            a = [y for _, y in px_hist[-HIST:]]
            if close is not None:
                p, a = p + [float(close)], a + [float(ad)]
            div = bm.breadth_divergence(p, a)
            row.update(
                mcclellan_osc=osc,
                mcclellan_sum=summ,
                spx_close=close,
                bull_bear_line=bbl,
                above_bbl=(close > bbl) if (close is not None and bbl is not None) else None,
                divergence_state=div.get("state"),
                divergence_len=div.get("length"),
            )
            if close is not None:
                px_hist.append((float(close), float(ad)))
        net_hist.append(net)
        rows.append(row)
    return rows
