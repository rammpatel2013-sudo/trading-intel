"""Convexity scanner (2026-11-22). Pure, no DB. Descriptor only (rule 4) - nothing here writes ``signals``.

Puts each S&P 1500 name with a real option IV (compass_daily iv_src = cvforge_bank / convex_eod, not the HV20
stand-in) into at most one of four lists, by price trend (equity_stages) x option price (compass_daily):

    cheap_up    calls cheap on a rising stock: weekly Stage 2 (or Stage 1 with a daily 2A/2B), Mansfield RS > 0,
                IV30 in the bottom 30% of its year (or no rank yet and IV30 <= HV10), IV30 - HV10 <= 2 vol pts,
                upside skew not already bid (skew rank unknown or >= 0.3).
    cheap_down  puts cheap on a falling stock: weekly Stage 4 or daily 4A/4B, RS < 0, IV rank <= 0.4,
                carry <= 3 vol pts, put skew not already rich (skew rank <= 0.6).
    rich_sell   premium rich on a strong stock: weekly Stage 2, daily not in Stage 3/4, IV rank >= 0.8,
                carry >= 5 vol pts and >= 30% of IV30, term not inverted (IV30 / IV90 <= 1.0).
    avoid       options underprice the moves on a falling stock: carry <= -5 vol pts, term inverted
                (IV30 / IV90 >= 1.05), weekly or daily Stage 4. Selling premium here is picking up nickels.

Names with earnings in the next EARN_DAYS calendar days are left out (event IV is not "rich", and an event
gap is not "cheap"). Liquidity: $ option premium >= MIN_PREM when known. Top TOP_N per list by score.

Paper track: every list is stored (equity_convex_picks) and scored at 5 / 10 / 20 sessions against equity_bars
with the move IV30 implied for that horizon (sigma_h = IV30 * sqrt(h / 252)):
    cheap_up   hit = the high touched spot * (1 + sigma_h)            (normal base rate ~32%)
    cheap_down hit = the low touched spot * (1 - sigma_h)             (~32%)
    rich_sell  hit = the close stayed >= spot * (1 - sigma_h)         (~84%; a short put at -1 sigma kept)
    avoid      hit = |close move| > sigma_h                           (~32%)
plus realised vol / IV30 over the window (>1 = options were cheap, <1 = rich). A list only earns trust if it beats
its base rate over many dates.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta

import numpy as np

BUCKETS = ("cheap_up", "cheap_down", "rich_sell", "avoid")
LABEL = {"cheap_up": "Cheap upside", "cheap_down": "Cheap downside", "rich_sell": "Rich premium",
         "avoid": "Avoid selling"}
REAL_IV = ("cvforge_bank", "convex_eod")
HORIZONS = (5, 10, 20)
BASE_RATE = {"cheap_up": 0.317, "cheap_down": 0.317, "rich_sell": 0.841, "avoid": 0.317}
EARN_DAYS, MIN_PREM, TOP_N = 25, 5e5, 15


def _f(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def move(iv30: float | None, h: int) -> float | None:
    return None if iv30 is None or iv30 <= 0 else iv30 * math.sqrt(h / 252.0)


def classify(r: dict) -> tuple[str, float] | None:
    """(bucket, score) for one merged row (compass_daily fields + code / wstage / mrs), or None."""
    iv, ivr, carry = _f(r.get("iv30")), _f(r.get("iv_rank")), _f(r.get("carry"))
    ts, skr, mrs = _f(r.get("ts_30_90")), _f(r.get("skew_rank")), _f(r.get("mrs"))
    ws, code = int(r.get("wstage") or 0), str(r.get("code") or "")
    if iv is None or carry is None or r.get("iv_src") not in REAL_IV:
        return None
    day_stage = code[:1]
    if carry <= -0.05 and ts is not None and ts >= 1.05 and (ws == 4 or day_stage == "4"):
        return "avoid", round(_clip(-carry * 10, 0, 2) + _clip((ts - 1) * 5, 0, 1), 3)
    if (ws == 2 and day_stage not in ("3", "4") and ivr is not None and ivr >= 0.8
            and carry >= 0.05 and carry >= 0.3 * iv and (ts is None or ts <= 1.0)):
        return "rich_sell", round(ivr + _clip(carry / iv) + _clip((mrs or 0) / 10), 3)
    cheap_iv = (ivr is not None and ivr <= 0.3) or (ivr is None and carry <= 0)
    if ((ws == 2 or (ws == 1 and code in ("2A", "2B"))) and mrs is not None and mrs > 0 and cheap_iv
            and carry <= 0.02 and (skr is None or skr >= 0.3)):
        return "cheap_up", round((0.5 - (ivr if ivr is not None else 0.3)) + _clip(mrs / 10) + _clip(-carry * 10), 3)
    if ((ws == 4 or code in ("4A", "4B")) and mrs is not None and mrs < 0 and ivr is not None and ivr <= 0.4
            and carry <= 0.03 and (skr is None or skr <= 0.6)):
        return "cheap_down", round((0.5 - ivr) + _clip(-mrs / 10) + _clip(-carry * 10), 3)
    return None


def pick(rows: Sequence[dict], day: date, earnings: dict[str, date] | None = None,
         top_n: int = TOP_N) -> tuple[list[dict], dict]:
    """(picks, counts). ``rows`` = merged per-name rows for ``day``; ``earnings`` = next report date per symbol."""
    earnings = earnings or {}
    horizon = day + timedelta(days=EARN_DAYS)
    out: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    skipped = {"earnings": 0, "illiquid": 0, "no_real_iv": 0}
    for r in rows:
        if r.get("grp") not in (None, "stock"):
            continue
        if r.get("iv_src") not in REAL_IV:
            skipped["no_real_iv"] += 1
            continue
        prem = _f(r.get("prem_usd"))
        if prem is not None and prem < MIN_PREM:
            skipped["illiquid"] += 1
            continue
        e = earnings.get(r["symbol"])
        if e is not None and day <= e <= horizon:
            skipped["earnings"] += 1
            continue
        c = classify(r)
        if c is None:
            continue
        b, sc = c
        iv = _f(r.get("iv30"))
        out[b].append({"symbol": r["symbol"], "bucket": b, "score": sc, "spot": _f(r.get("close")), "iv30": iv,
                       "iv_rank": _f(r.get("iv_rank")), "hv10": _f(r.get("hv10")), "carry": _f(r.get("carry")),
                       "ts_30_90": _f(r.get("ts_30_90")), "skew25": _f(r.get("skew25")),
                       "skew_rank": _f(r.get("skew_rank")), "code": r.get("code"), "wstage": r.get("wstage"),
                       "mrs": _f(r.get("mrs")), "ret20": _f(r.get("ret20")), "move20": move(iv, 20),
                       "next_earn": e.isoformat() if e else None})
    picks: list[dict] = []
    counts = {"skipped": skipped}
    for b in BUCKETS:
        lst = sorted(out[b], key=lambda p: -p["score"])
        counts[b] = len(lst)
        for i, p in enumerate(lst[:top_n], 1):
            picks.append({**p, "rank": i, "date": day})
    return picks, counts


def score(p: dict, closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], h: int) -> dict | None:
    """Score one pick over the first ``h`` sessions after its date (bars strictly after). None if not enough bars."""
    spot, sig = _f(p.get("spot")), move(_f(p.get("iv30")), h)
    if spot is None or sig is None or len(closes) < h:
        return None
    c = np.asarray(closes[:h], float)
    hi = np.asarray([x if x is not None else np.nan for x in highs[:h]], float)
    lo = np.asarray([x if x is not None else np.nan for x in lows[:h]], float)
    hi = np.where(np.isfinite(hi), hi, c)
    lo = np.where(np.isfinite(lo), lo, c)
    last = float(c[-1]) / spot - 1
    b = p["bucket"]
    if b == "cheap_up":
        hit = float(np.nanmax(hi)) >= spot * (1 + sig)
    elif b == "cheap_down":
        hit = float(np.nanmin(lo)) <= spot * (1 - sig)
    elif b == "rich_sell":
        hit = last >= -sig
    else:
        hit = abs(last) > sig
    path = np.concatenate([[spot], c])
    lr = np.diff(np.log(path))
    rv = float(np.sqrt(np.mean(lr ** 2) * 252)) if len(lr) else None
    iv = _f(p.get("iv30"))
    return {"hit": bool(hit), "ret": round(last, 4), "sig": round(sig, 4),
            "rv_iv": None if rv is None or not iv else round(rv / iv, 3)}


def track(picks: Sequence[dict], bars: dict[str, list[tuple[date, float, float | None, float | None]]]) -> dict:
    """Hit rate + mean realised/implied per bucket per horizon. ``bars[sym]`` = sorted (date, close, high, low)."""
    agg: dict = {b: {h: {"n": 0, "hits": 0, "rv_iv": []} for h in HORIZONS} for b in BUCKETS}
    dates: set = set()
    for p in picks:
        after = [x for x in bars.get(p["symbol"], []) if x[0] > p["date"]]
        for h in HORIZONS:
            s = score(p, [x[1] for x in after], [x[2] for x in after], [x[3] for x in after], h)
            if s is None:
                continue
            a = agg[p["bucket"]][h]
            a["n"] += 1
            a["hits"] += int(s["hit"])
            if s["rv_iv"] is not None:
                a["rv_iv"].append(s["rv_iv"])
            dates.add(p["date"])
    out: dict = {"dates_scored": len(dates), "base": BASE_RATE, "rows": []}
    for b in BUCKETS:
        for h in HORIZONS:
            a = agg[b][h]
            out["rows"].append({"bucket": b, "h": h, "n": a["n"],
                                "hit": None if not a["n"] else round(a["hits"] / a["n"], 3),
                                "base": BASE_RATE[b],
                                "rv_iv": None if not a["rv_iv"] else round(float(np.median(a["rv_iv"])), 3)})
    return out


def vol_board(rows: Sequence[dict]) -> list[dict]:
    """Cross-asset ETF vol board from compass rows (grp != stock): VRP, term, rank, a strategy-map quadrant."""
    out = []
    for r in rows:
        g = r.get("grp")
        if g in (None, "stock"):
            continue
        iv, hv10, hv30 = _f(r.get("iv30")), _f(r.get("hv10")), _f(r.get("hv30"))
        ivr, ts, ret20 = _f(r.get("iv_rank")), _f(r.get("ts_30_90")), _f(r.get("ret20"))
        vrp = None if iv is None or hv30 is None else iv - hv30
        z20 = None if ret20 is None or not iv else ret20 / (iv * math.sqrt(20 / 252))
        if ivr is None or vrp is None:
            quad = None
        elif ivr >= 0.5 and vrp > 0:
            quad = "sell premium"
        elif ivr < 0.5 and vrp <= 0:
            quad = "buy options"
        elif ivr >= 0.5:
            quad = "high IV, moving - stand aside"
        else:
            quad = "cheap, quiet - spreads"
        out.append({"symbol": r["symbol"], "grp": g, "iv30": iv, "hv10": hv10, "hv30": hv30, "vrp": vrp,
                    "iv_rank": ivr, "ts_30_90": ts, "ret20": ret20, "z20": None if z20 is None else round(z20, 2),
                    "iv_src": r.get("iv_src"), "quad": quad})
    return out
