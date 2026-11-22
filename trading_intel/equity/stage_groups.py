"""Weekly (30-week) stages and industry-group stages (2026-11-22). Pure numpy, no DB. Descriptor only (rule 4).

Weekly stage - Stan Weinstein's four stages on weekly closes, as on stageanalysis.net:
    week close = the last session's close in each calendar week (the current week = "so far")
    MA30  = 30-week simple average;  slope = MA30 now / MA30 four weeks ago - 1
    rising  = slope >  FLAT (1%)          falling = slope < -FLAT          else flat
    Stage 2  rising average, close above it          Stage 3  rising average, close below it (losing grip)
    Stage 4  falling average, close below it         Stage 1  falling average, close above it (early base)
    flat average: Stage 3 if the last stage was 2 or 3 (topping), else Stage 1 (basing)
    Mansfield relative strength = (stock / SPY) / its 52-week average - 1, in %; above 0 = beating the market.
A day uses the stage of its own week if it is that week's last session, else last week's (no peeking ahead).

Industry groups - the daily Candle Stage mix (equity/stages.py) of the members in each FMP industry with 4+
members: share rising (Stage 2) and falling (Stage 4) now and 20 sessions ago. INDPOS = % of groups with more
rising than falling stocks, per day - breadth of groups, not of stocks.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from trading_intel.equity.stages import sma

FLAT, MA_W, SLOPE_W, RS_W, MIN_IND = 0.01, 30, 4, 52, 4


def week_ends(dates: list[date]) -> list[int]:
    """Index of the last session of each ISO week (the final, maybe partial, week included)."""
    wk = [d.isocalendar()[:2] for d in dates]
    return [i for i in range(len(dates)) if i == len(dates) - 1 or wk[i + 1] != wk[i]]


def weekly_stage(wclose: np.ndarray) -> np.ndarray:
    """Weekly stage 0-4 for a week x symbol array (0 = under 34 weeks of history)."""
    ma = sma(wclose, MA_W)
    prev = np.vstack([np.full((SLOPE_W, wclose.shape[1]), np.nan), ma[:-SLOPE_W]])
    with np.errstate(all="ignore"):
        slope = ma / prev - 1
    stage = np.zeros(wclose.shape, dtype=np.int8)
    last = np.zeros(wclose.shape[1], dtype=np.int8)
    for w in range(wclose.shape[0]):
        ok = np.isfinite(slope[w]) & np.isfinite(wclose[w])
        above = wclose[w] > ma[w]
        rising, falling = slope[w] > FLAT, slope[w] < -FLAT
        s = np.where(rising, np.where(above, 2, 3),
                     np.where(falling, np.where(above, 1, 4), np.where((last == 2) | (last == 3), 3, 1)))
        s = np.where(ok, s, 0).astype(np.int8)
        stage[w] = s
        last = np.where(ok, s, last)
    return stage


def mansfield(wclose: np.ndarray, wspy: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        rs = wclose / wspy[:, None]
        return (rs / sma(rs, RS_W) - 1) * 100


def daily_weekly(dates: list[date], close: np.ndarray, spy: np.ndarray | None) -> tuple[np.ndarray, np.ndarray]:
    """(weekly stage, Mansfield RS) per day x symbol, each day taking its own week only on that week's last
    session, else last week's (no peeking ahead). Stage 0 / NaN where there is no history."""
    ends = week_ends(dates)
    wst = weekly_stage(close[ends])
    mrs = mansfield(close[ends], spy[ends]) if spy is not None else np.full(wst.shape, np.nan)
    wk_of_day = np.searchsorted(np.array(ends), np.arange(len(dates)))
    use = np.where(np.isin(np.arange(len(dates)), ends), wk_of_day, wk_of_day - 1)
    st = np.zeros(close.shape, dtype=np.int8)
    rs = np.full(close.shape, np.nan)
    ok = use >= 0
    st[ok] = wst[use[ok]]
    rs[ok] = mrs[use[ok]]
    return st, rs


def weekly(dates: list[date], symbols: list[str], close: np.ndarray, member: np.ndarray, spy: np.ndarray | None,
           sector: list, mcap: np.ndarray | None) -> tuple[dict, dict[str, np.ndarray]]:
    """(page block, daily gauges {"WS2", "WS4"}) - shares of members in weekly Stage 2 / Stage 4."""
    ends = week_ends(dates)
    wst = weekly_stage(close[ends])
    wmem = member[ends] & (wst > 0)
    wk_of_day = np.searchsorted(np.array(ends), np.arange(len(dates)))      # the week each day belongs to
    use = np.where(np.isin(np.arange(len(dates)), ends), wk_of_day, wk_of_day - 1)
    gauges = {}
    for k, lab in ((2, "WS2"), (4, "WS4")):
        n = wmem.sum(axis=1).astype(float)
        with np.errstate(all="ignore"):
            share_w = np.where(n > 0, 100.0 * ((wst == k) & wmem).sum(axis=1) / n, np.nan)
        share_w[n < 20] = np.nan
        g = np.full(len(dates), np.nan)
        g[use >= 0] = share_w[use[use >= 0]]
        gauges[lab] = g
    n = wmem.sum(axis=1).astype(float)
    with np.errstate(all="ignore"):
        mix = {f"s{k}": [None if not (v > 0) else round(100.0 * float(((wst[w] == k) & wmem[w]).sum()) / v, 1)
                         for w, v in enumerate(n)][-156:] for k in (1, 2, 3, 4)}
    mix["dates"] = [dates[i].isoformat() for i in ends][-156:]
    w = len(ends) - 1
    mrs = mansfield(close[ends], spy[ends]) if spy is not None else np.full(wst.shape, np.nan)
    mc = mcap[ends[-1]] if mcap is not None else np.full(len(symbols), np.nan)
    new2 = (wst[w] == 2) & (wst[w - 1] != 2) & wmem[w] if w >= 1 else np.zeros(len(symbols), bool)
    new4 = (wst[w] == 4) & (wst[w - 1] != 4) & wmem[w] if w >= 1 else np.zeros(len(symbols), bool)

    def pick(m: np.ndarray, strong: bool) -> list[dict]:
        idx = [i for i in np.where(m)[0] if not strong or (np.isfinite(mrs[w, i]) and mrs[w, i] > 0)]
        idx = sorted(idx, key=lambda i: -(mc[i] if np.isfinite(mc[i]) else 0))[:20]
        return [{"symbol": symbols[i], "sector": sector[i],
                 "rs": None if not np.isfinite(mrs[w, i]) else round(float(mrs[w, i]), 1),
                 "mcap_bn": None if not np.isfinite(mc[i]) else round(float(mc[i]) / 1e9, 1)} for i in idx]
    block = {"asof_week": mix["dates"][-1] if mix["dates"] else None, "mix": mix,
             "new2": pick(new2, True), "new4": pick(new4, False),
             "counts": {"new2": int(new2.sum()), "new2_strong": int(sum(1 for i in np.where(new2)[0]
                                                                         if np.isfinite(mrs[w, i]) and mrs[w, i] > 0)),
                        "new4": int(new4.sum())}}
    return block, gauges


def industries(dates: list[date], industry: list, sector: list, main: np.ndarray,
               member: np.ndarray) -> tuple[list[dict], np.ndarray]:
    """(group rows, sorted strongest first; daily INDPOS series)."""
    t = len(dates) - 1
    names = sorted({x for x in industry if x})
    pos = np.zeros(len(dates))
    cnt = np.zeros(len(dates))
    rows = []
    for nm in names:
        cols = np.array([x == nm for x in industry])
        m = member & cols[None, :]
        n = m.sum(axis=1).astype(float)
        with np.errstate(all="ignore"):
            s2 = np.where(n >= MIN_IND, 100.0 * ((main == 2) & m).sum(axis=1) / n, np.nan)
            s4 = np.where(n >= MIN_IND, 100.0 * ((main == 4) & m).sum(axis=1) / n, np.nan)
        ok = np.isfinite(s2)
        cnt += ok
        pos += ok & (s2 > s4)
        if not ok[t]:
            continue
        secs = [sector[i] for i in np.where(cols & member[t])[0] if sector[i]]
        then = t - 20 if t >= 20 and np.isfinite(s2[t - 20]) else None
        rows.append({"name": nm, "sector": max(set(secs), key=secs.count) if secs else None, "n": int(n[t]),
                     "s2": round(float(s2[t]), 1), "s4": round(float(s4[t]), 1),
                     "net": round(float(s2[t] - s4[t]), 1),
                     "net_20d_ago": None if then is None else round(float(s2[then] - s4[then]), 1)})
    rows.sort(key=lambda r: (-r["net"], -r["s2"]))
    with np.errstate(all="ignore"):
        indpos = np.where(cnt >= 10, 100.0 * pos / cnt, np.nan)
    return rows, indpos
