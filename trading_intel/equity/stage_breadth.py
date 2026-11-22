"""Stage breadth (2026-11-22): S&P 1500 stage mix, stage-based breadth gauges, today's breakouts / breakdowns,
sector stage mix and sector rotation -> one dict (the job writes it to scripts/cvxmap/data/stagetab.json for the
Risk page). Pure numpy on a Panel-like set of arrays; no DB, no network. Descriptor only (CLAUDE.md rule 4).

Gauges (share of index members that day, in %):
    stage2   in Stage 2 (2A-2D)                       stage4   in Stage 4 (4A-4C)
    net5     entered Stage 2 minus entered Stage 4 over the last 5 sessions (% of members)
    ext      stretched far above trend (2C / 2D, 7+ ATR-percent units above the 50-day)
    woe      above the 50-, 150- and 200-day averages, the three shares averaged ("weight of evidence",
             stageanalysis.net's combined line)
Each gauge carries the same "year view" as the zone cards: where today sits in its past year, the year's normal
band, and how often SPY fell 3%+ within 5 sessions after days in the gauge's top / bottom 10% of its year.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from trading_intel.equity import stage_groups as sg
from trading_intel.equity.rotation import GICS_TO_ETF, SECTOR_ETFS, rotation
from trading_intel.equity.stages import CODES, NAMES, classify, main_stage, sma

YEAR, LONGV, DROP5 = 252, 756, 3.0


def _share(mask: np.ndarray, member: np.ndarray) -> np.ndarray:
    n = member.sum(axis=1).astype(float)
    with np.errstate(all="ignore"):
        return np.where(n > 0, 100.0 * (mask & member).sum(axis=1) / n, np.nan)


def drops5(spy: np.ndarray) -> list:
    n = len(spy)
    return [None if t + 5 >= n or not np.isfinite(spy[t]) else bool((np.nanmin(spy[t + 1:t + 6]) / spy[t] - 1) * 100 <= -DROP5)
            for t in range(n)]


def year_view(x: np.ndarray, d5: list) -> dict:
    """Same shape as zone_cards.year_view (scripts/zone_cards.py) so the Risk page reads both alike."""
    last = len(x) - 1
    rk = [None] * len(x)
    for t in range(YEAR // 2, len(x)):
        w = x[max(0, t - YEAR):t]
        w = w[np.isfinite(w)]
        if np.isfinite(x[t]) and len(w) >= YEAR // 2:
            rk[t] = float((w < x[t]).mean())
    w = np.sort(x[max(0, last - YEAR):last][np.isfinite(x[max(0, last - YEAR):last])])
    q = (lambda f: float(w[min(len(w) - 1, int(f * (len(w) - 1) + 0.5))])) if len(w) else (lambda f: None)
    base = [d for d in d5 if d is not None]
    rec = {}
    for side, ok in (("top", lambda r: r >= 0.9), ("bottom", lambda r: r <= 0.1)):
        on = [d5[t] for t in range(len(rk)) if rk[t] is not None and ok(rk[t]) and d5[t] is not None]
        rec[side] = {"n": len(on), "rate": round(sum(on) / len(on), 3) if len(on) >= 30 else None}
    t0 = max(0, last - LONGV + 1)
    return {"rank": None if rk[last] is None else round(rk[last], 3), "q10": q(0.1), "q25": q(0.25), "q75": q(0.75),
            "q90": q(0.9), "base5": round(sum(base) / len(base), 3) if base else None, "rec": rec,
            "vals": [None if not np.isfinite(v) else round(float(v), 2) for v in x[t0:]],
            "v2wk": None if last < 10 or not np.isfinite(x[last - 10]) else round(float(x[last - 10]), 2)}


def _z50(x: np.ndarray) -> float | None:
    w = x[-51:-1]
    w = w[np.isfinite(w)]
    if len(w) < 40 or not np.isfinite(x[-1]) or w.std() == 0:
        return None
    return round(float((x[-1] - w.mean()) / w.std()), 2)


def compute(dates: list[date], symbols: list[str], close: np.ndarray, high: np.ndarray, low: np.ndarray,
            member: np.ndarray, sector: list, mcap: np.ndarray | None, extra: dict[str, np.ndarray],
            code: np.ndarray | None = None, xatr: np.ndarray | None = None, industry: list | None = None) -> dict:
    """extra = closes for SPY + the sector funds on the same dates (NaN where missing). code / xatr from
    stages.classify when the caller already has them. industry = FMP industry per symbol (None = no group view)."""
    if code is None or xatr is None:
        code, xatr = classify(close, high, low)
    main = main_stage(code)
    member = member & (code > 0)
    iso = [d.isoformat() if hasattr(d, "isoformat") else str(d) for d in dates]
    shares = {k: _share(main == k, member) for k in (1, 2, 3, 4)}
    s50, s150, s200 = sma(close, 50), sma(close, 150), sma(close, 200)
    with np.errstate(all="ignore"):
        woe = (_share(close > s50, member & np.isfinite(s50)) + _share(close > s150, member & np.isfinite(s150))
               + _share(close > s200, member & np.isfinite(s200))) / 3
    ent2 = (main == 2) & np.vstack([np.zeros((1, main.shape[1]), bool), main[:-1] != 2]) & member
    ent4 = (main == 4) & np.vstack([np.zeros((1, main.shape[1]), bool), main[:-1] != 4]) & member
    n_mem = member.sum(axis=1).astype(float)
    with np.errstate(all="ignore"):
        net_d = np.where(n_mem > 0, 100.0 * (ent2.sum(axis=1) - ent4.sum(axis=1)) / n_mem, np.nan)
    net5 = np.convolve(np.nan_to_num(net_d), np.ones(5), "full")[: len(net_d)]
    net5[:5] = np.nan
    ext = _share((code == 5) | (code == 6), member)
    spy = extra.get("SPY")
    d5 = drops5(spy) if spy is not None else [None] * len(dates)
    gauges = {"STAGE2": shares[2], "STAGE4": shares[4], "NET5": net5, "EXT": ext, "WOE": woe}
    wk, wg = sg.weekly(list(dates), symbols, close, member, spy, sector, mcap)
    gauges.update(wg)
    groups, indpos = sg.industries(list(dates), industry, sector, main, member) if industry else ([], None)
    if indpos is not None:
        gauges["INDPOS"] = indpos
    dates3y = iso[-LONGV:]
    cards = [{"id": k, "value": None if not np.isfinite(v[-1]) else round(float(v[-1]), 2), "z": _z50(v),
              "year": year_view(v, d5), "dates3y": dates3y} for k, v in gauges.items()]
    t = len(dates) - 1
    mix = {"dates": iso[-LONGV:], **{f"s{k}": [None if not np.isfinite(v) else round(float(v), 1) for v in shares[k][-LONGV:]]
                                     for k in (1, 2, 3, 4)}}
    # today's lists: new breakouts (2B today, not Stage 2 five sessions ago) and breakdowns (4B, not Stage 4)
    prior = main[t - 5] if t >= 5 else np.zeros_like(main[t])
    mc = mcap[t] if mcap is not None else np.full(len(symbols), np.nan)

    def pick(m: np.ndarray) -> list[dict]:
        idx = np.where(m & member[t])[0]
        idx = sorted(idx, key=lambda i: -(mc[i] if np.isfinite(mc[i]) else 0))[:20]
        return [{"symbol": symbols[i], "sector": sector[i], "xatr": None if not np.isfinite(xatr[t, i]) else round(float(xatr[t, i]), 1),
                 "ret20": None if t < 20 or not np.isfinite(close[t - 20, i]) else round(float(close[t, i] / close[t - 20, i] - 1) * 100, 1),
                 "mcap_bn": None if not np.isfinite(mc[i]) else round(float(mc[i]) / 1e9, 1)} for i in idx]
    breakouts = pick((code[t] == 4) & (prior != 2))
    breakdowns = pick((code[t] == 10) & (prior != 4))
    counts = {"breakouts": int(((code[t] == 4) & (prior != 2) & member[t]).sum()),
              "breakdowns": int(((code[t] == 10) & (prior != 4) & member[t]).sum())}
    # sectors: stage mix today + Stage 2 share now vs 20 sessions ago
    secs = []
    with np.errstate(all="ignore"):
        hi = np.nanmax(close[max(0, t - 251):t + 1], axis=0)           # 52-week high close
        dd = close[t] / hi - 1
        r20 = close[t] / close[t - 20] - 1 if t >= 20 else np.full(len(symbols), np.nan)
    for etf, name in SECTOR_ETFS.items():
        cols = np.array([GICS_TO_ETF.get(s or "") == etf for s in sector])
        if not cols.any():
            continue
        m_now = member[t] & cols
        n = int(m_now.sum())
        if n < 5:
            continue
        mix_now = {f"s{k}": round(100.0 * ((main[t] == k) & m_now).sum() / n, 1) for k in (1, 2, 3, 4)}
        if t >= 20:
            m_then = member[t - 20] & cols
            s2_then = round(100.0 * ((main[t - 20] == 2) & m_then).sum() / max(1, int(m_then.sum())), 1)
        else:
            s2_then = None
        d_ok = m_now & np.isfinite(dd)
        r_ok = m_now & np.isfinite(r20)
        secs.append({"etf": etf, "name": name, "n": n, **mix_now, "s2_20d_ago": s2_then,
                     "dd20": None if not d_ok.any() else round(100.0 * float((dd[d_ok] <= -0.2).mean()), 1),
                     "r20": None if not r_ok.any() else round(100.0 * float(np.median(r20[r_ok])), 1)})
    secs.sort(key=lambda r: -r["s2"])
    rot = rotation(iso, extra) if "SPY" in extra else []
    by_code = {CODES[k]: int(((code[t] == k) & member[t]).sum()) for k in range(1, 12)}
    return {"asof": iso[t], "start": iso[0], "n_members": int(member[t].sum()), "cards": cards, "mix": mix,
            "codes": by_code, "code_names": NAMES, "breakouts": breakouts, "breakdowns": breakdowns, "counts": counts,
            "sectors": secs, "rotation": rot, "weekly": wk, "industries": groups}


def stage_rows(dates: list[date], symbols: list[str], code: np.ndarray, xatr: np.ndarray, member: np.ndarray,
               since: date | None = None, wstage: np.ndarray | None = None,
               mrs: np.ndarray | None = None) -> list[dict]:
    """Rows for the equity_stages table (members only, from `since`); weekly stage / Mansfield RS when given."""
    out = []
    main = main_stage(code)
    ws = wstage if wstage is not None else np.zeros(code.shape, dtype=np.int8)
    rs = mrs if mrs is not None else np.full(code.shape, np.nan)
    for t, d in enumerate(dates):
        if since and d < since:
            continue
        for i in np.where(member[t] & (code[t] > 0))[0]:
            out.append({"date": d, "symbol": symbols[i], "code": CODES[int(code[t, i])], "stage": int(main[t, i]),
                        "xatr": None if not np.isfinite(xatr[t, i]) else round(float(xatr[t, i]), 2),
                        "wstage": int(ws[t, i]) or None,
                        "mrs": None if not np.isfinite(rs[t, i]) else round(float(rs[t, i]), 2)})
    return out
