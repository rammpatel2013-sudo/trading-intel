"""odte_card.py - created 2026-11-14, rebuilt the same day on our own prices. Data for the SPX 0DTE map card.

The AM card (Mithil, 2026-11-14 15:20): the day's open interest (posted overnight, in the 08:35 book), the
08:35 FORWARD (call minus put prices - where futures pointed before the open), a straddle from real prices,
and the gamma profile centred on that forward. Plus: is the OI really new, today's 0DTE pinned, AM-settled
contracts (monthly + SPXO) shown before and after the 09:30 open, the overnight change (price gap, vol
change ranked against every morning since October, biggest position changes), the dealer-side assumption
named, and every number's source. Vendor IVs are NOT used (market/clean_book.py says why).
Descriptor, not a signal (rule 4).
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market import clean_book as cb
from trading_intel.market.book_spot import book_spot
from trading_intel.mcp import event_tools as ev
from trading_intel.mcp import profile_tool as pt
from trading_intel.memory.models import GreeksSnapshot, IvTenorSnapshot
from trading_intel.timeutils import live_expiry_floor

OI_DAYS = 7               # levels come from open interest in the next 7 days of expiries
HISTORY_BOOKS = 30        # mornings used to rank the overnight vol change
SPIKE_PTS, SPIKE_PCT = 2.0, 90.0
DEALER_SIGN = "naive: dealers long the calls, short the puts"


def pick_levels(oi: dict[float, dict[str, float]], centre: float, band: float,
                min_gap: float) -> dict[str, Any]:
    """Cap = most call OI above the centre, floor = most put OI below it, steps = the (up to 4) biggest other
    strikes with >= 35% of the biggest total OI, air pockets = gaps of >= ``min_gap`` points between neighbouring levels with no
    strike over 15% of that biggest total - all within ``band`` points of the centre."""
    near = {k: v for k, v in oi.items() if abs(k - centre) <= band}
    if not near:
        return {"cap": None, "floor": None, "steps": [], "air_pockets": []}
    tot = {k: v["C"] + v["P"] for k, v in near.items()}
    top = max(tot.values()) or 1.0
    above = [k for k in near if k > centre]
    below = [k for k in near if k < centre]
    cap = max(above, key=lambda k: near[k]["C"]) if above else None
    flo = max(below, key=lambda k: near[k]["P"]) if below else None
    big = sorted((k for k, t in tot.items() if t >= 0.35 * top and k not in (cap, flo)), key=lambda k: -tot[k])
    steps = sorted(big[:4])                    # at most 4 steps: the map stays readable on a phone
    marks = sorted({*steps, *([cap] if cap else []), *([flo] if flo else [])})
    pockets = []
    for a, b in zip(marks, marks[1:], strict=False):
        inner = [tot[k] for k in tot if a < k < b]
        if b - a >= min_gap and (not inner or max(inner) < 0.15 * top):
            pockets.append([a, b])
    lv = lambda k: {"strike": k, "call_oi": round(near[k]["C"]), "put_oi": round(near[k]["P"])}  # noqa: E731
    return {"cap": lv(cap) if cap else None, "floor": lv(flo) if flo else None,
            "steps": [lv(k) for k in steps], "air_pockets": pockets, "biggest_total_oi": round(top)}


def regime(centre: float, switch: float | None, board: float | None) -> dict[str, str]:
    lv = [x for x in (switch, board) if x is not None]
    if lv and all(centre < x for x in lv):
        return {"key": "neg", "line": "NEGATIVE GAMMA · BELOW BOTH SWITCHES · MOVES FEED THEMSELVES"}
    if lv and all(centre > x for x in lv):
        return {"key": "pos", "line": "POSITIVE GAMMA · ABOVE BOTH SWITCHES · MOVES CALM"}
    return {"key": "mid", "line": "BETWEEN THE TWO SWITCHES · MIXED · WATCH WHICH ONE BREAKS"}


def if_then(centre: float, lv: dict[str, Any], switch: float | None, board: float | None,
            r80: list[float], day_name: str = "today") -> list[dict[str, str]]:
    """Plain if/then rows keyed up / range / down (descriptors, not signals)."""
    f = lambda x: f"{x:,.0f}"  # noqa: E731
    rows = []
    ups = sorted({x for x in (switch, board) if x is not None and x > centre})
    for x in ups:
        name = f"{day_name}'s switch" if x == switch else "the board switch"
        what = ("moves slow between the switches" if x == switch and board and board > x
                else "the whole board turns positive gamma; pushes calm")
        rows.append({"icon": "up", "if": f"Back over {f(x)}", "then": f"{name[0].upper()}{name[1:]}: {what}."})
    cap = (lv.get("cap") or {}).get("strike")
    if cap:
        rows.append({"icon": "up", "if": f"Near {f(cap)}",
                     "then": f"Cap: the most call open interest above price ({lv['cap']['call_oi']:,})."})
    flo = (lv.get("floor") or {}).get("strike")
    steps_below = sorted((s["strike"] for s in lv.get("steps", []) if s["strike"] < centre), reverse=True)
    hold_lo = steps_below[0] if steps_below else flo
    hold_hi = min(ups) if ups else cap
    if hold_lo and hold_hi:
        rows.append({"icon": "range", "if": f"Holds {f(hold_lo)} to {f(hold_hi)}",
                     "then": "Range day between the nearest levels."})
    if steps_below and flo and steps_below[0] != flo:
        rows.append({"icon": "down", "if": f"Loses {f(steps_below[0])}",
                     "then": f"Next big put level is the {f(flo)} floor ({lv['floor']['put_oi']:,} put OI)."})
    if flo:
        rows.append({"icon": "down", "if": f"Loses {f(flo)}",
                     "then": f"Below the floor; the 80% close range ends at {f(r80[0])}. "
                             + ("Under the switches dealers are short gamma: their hedging adds to the move."
                                if flo < min(x for x in (switch, board, centre) if x is not None)
                                else "Dealers' hedging still cushions it.")})
    return rows


def _oi_by_strike(smiles: dict, lo: date, hi: date) -> dict[float, dict[str, float]]:
    out: dict[float, dict[str, float]] = {}
    for (e, _am), sm in smiles.items():
        if not lo <= e <= hi:
            continue
        for k, v in sm.oi_call.items():
            out.setdefault(k, {"C": 0.0, "P": 0.0})["C"] += v
        for k, v in sm.oi_put.items():
            out.setdefault(k, {"C": 0.0, "P": 0.0})["P"] += v
    return out


def oi_overnight(today: list[dict[str, Any]], prev: list[dict[str, Any]], day: date, centre: float,
                 band: float, front_hi: date) -> dict[str, Any]:
    """Did open interest update overnight (share of later-dated contracts that changed; a normal night moves
    37-46%), and the biggest changes near the centre in the front week."""
    key = lambda r: (r["expiry"], float(r["strike"]), str(r["cp"]).upper()[:1], str(r["source"]))  # noqa: E731
    p = {key(r): float(r["oi"] or 0) for r in prev}
    later = [(key(r), float(r["oi"] or 0)) for r in today if (r["expiry"] - day).days >= 7 and key(r) in p]
    changed = sum(1 for k, v in later if v != p[k])
    share = 100.0 * changed / len(later) if later else None
    delta: dict[tuple[float, str], float] = {}
    for r in today:
        if r["expiry"] <= front_hi and abs(float(r["strike"]) - centre) <= band:
            k = key(r)
            d = float(r["oi"] or 0) - p.get(k, 0.0)
            kk = (float(r["strike"]), k[2])
            delta[kk] = delta.get(kk, 0.0) + d
    top = sorted(delta.items(), key=lambda x: -abs(x[1]))[:3]
    return {"changed_pct": round(share, 1) if share is not None else None,
            "updated": share is not None and share >= 15.0,
            "top": [{"strike": k, "side": "calls" if s == "C" else "puts", "change": round(d)}
                    for (k, s), d in top if d]}


def vol_change(today: tuple[float | None, float | None], hist: list[tuple[date, float | None, float | None]]
               ) -> dict[str, Any]:
    """Today's 7-day / 30-day ATM IV vs the previous morning, ranked against every morning-to-morning change
    in ``hist`` (newest first). Spike = top 10% or +2 vol pts; crush = bottom 10% or -2."""
    out: dict[str, Any] = {}
    for i, name in ((1, "7d"), (2, "30d")):
        now = today[i - 1]
        series = [h[i] for h in hist if h[i] is not None]
        if now is None or not series:
            out[name] = None
            continue
        chg = 100.0 * (now - series[0])
        diffs = [100.0 * (a - b) for a, b in zip(series, series[1:], strict=False)]
        pct = 100.0 * sum(1 for d in diffs if d <= chg) / len(diffs) if diffs else None
        flag = "normal"
        if chg >= SPIKE_PTS or (pct is not None and pct >= SPIKE_PCT and chg > 0):
            flag = "spike"
        elif chg <= -SPIKE_PTS or (pct is not None and pct <= 100 - SPIKE_PCT and chg < 0):
            flag = "crush"
        out[name] = {"now": round(100 * now, 2), "prev": round(100 * series[0], 2), "change": round(chg, 2),
                     "pct_rank": round(pct) if pct is not None else None, "n": len(diffs), "flag": flag}
    return out


def _latest_spot(session: Session, sym: str) -> tuple[float, datetime] | None:
    row = session.execute(select(GreeksSnapshot.spot, GreeksSnapshot.ts).where(
        GreeksSnapshot.symbol == sym, GreeksSnapshot.source == "convex", GreeksSnapshot.spot.isnot(None))
        .order_by(GreeksSnapshot.ts.desc()).limit(1)).first()
    return (float(row[0]), row[1].replace(tzinfo=None)) if row else None


def _since_close(session: Session, sym: str, prev_day: date, now30: float | None) -> dict[str, Any] | None:
    """30-day ATM IV now (ours, 08:35) vs the previous day's 17:05 snapshot (vendor IV, iv_tenor_snapshots).
    Different source and method - shown as a second, labelled line, never as the main number."""
    if now30 is None:
        return None
    row = session.execute(select(IvTenorSnapshot.iv_atm).where(
        IvTenorSnapshot.symbol == sym, IvTenorSnapshot.tenor_dte == 30,
        IvTenorSnapshot.ts == prev_day)).first()
    if not row or row[0] is None:
        return None
    close = float(row[0])
    return {"close": round(100 * close, 2), "now": round(100 * now30, 2), "change": round(100 * (now30 - close), 2),
            "source": "previous day 17:05 snapshot (vendor IV) vs our 08:35 IV"}


def build_odte_card(session: Session, symbol: str = "SPX", as_of: date | None = None, *,
                    live_rows: list[dict[str, Any]] | None = None,
                    live_dt: datetime | None = None) -> dict[str, Any]:
    """The card from ONE book and ONE centre (the book's own forward). ``live_rows`` + ``live_dt`` = a live
    chain (the 09:45 refresh) instead of the stored book; open interest is the same morning OI."""
    sym = symbol.upper()
    ts = pt._latest_oi_ts(session, sym, as_of)
    if ts is None:
        return {"symbol": sym, "found": False, "reason": "no options book"}
    day = ts.date()
    bs = book_spot(session, sym, ts)
    if bs is None:
        return {"symbol": sym, "found": False, "reason": "no spot for the book"}
    kind = bs["kind"]
    prior = float(bs["spot"])
    stored = cb.book_rows(session, sym, ts, max_days=60)
    if live_rows is not None and live_dt is not None:
        rows, bdt, floor, label = live_rows, live_dt, live_dt.date(), f"LIVE {live_dt:%H:%M}"
    else:
        rows, bdt, floor = stored, cb.book_time(kind, day), live_expiry_floor(day)
        label = f"{day:%a %m/%d} {'08:35' if kind == 'am' else 'close'} book"
    sms = cb.build_smiles(rows, bdt)
    fr = cb.front(sms, floor)
    if fr is None or fr.forward is None or abs(fr.forward - prior) > 0.05 * prior:
        return {"symbol": sym, "found": False, "reason": "no clean front-expiry prices in the book"}
    fwd = float(fr.forward)
    strd, strd_k = cb.straddle_mid(fr)
    atm = cb.atm_iv(fr) or 0.15
    strd = float(strd) if strd else fwd * atm * math.sqrt(fr.t) * 0.8

    # close odds on today's front expiry, from our own smile (smoothed), centred on the forward
    sk, sv = ev.smooth_smile(fr.strikes, fr.ivs, fwd, max(5.0, 0.3 * strd))
    g_s, surv = ev.survival_from_smile(sk, sv, fwd, fr.t)
    st = ev.dist_stats(g_s, surv, fwd, strd)
    why = ev.odds_quality(st, fwd, strd) + ev.shape_check(g_s, surv, fwd, strd, atm, fr.t)
    quality = "smile"
    if why:
        g_s, surv = ev.survival_from_smile(np.array([fwd * 0.8, fwd, fwd * 1.2]), np.full(3, atm), fwd, fr.t)
        st = ev.dist_stats(g_s, surv, fwd, strd)
        quality = "fallback_lognormal"
    ranges = {"50%": [round(st["p25"], 1), round(st["p75"], 1)], "70%": [round(st["p15"], 1), round(st["p85"], 1)],
              "80%": [round(st["p10"], 1), round(st["p90"], 1)]}

    # gamma on our IVs, centred on the forward; AM-settled contracts expiring today vanish at the 09:30 open
    grid = np.linspace(fwd * 0.97, fwd * 1.03, 241)
    board_c = cb.gamma_curve(sms, grid)
    zero_c = cb.gamma_curve(sms, grid, expiries={fr.expiry}, include_am=False)
    board = ev._flip_near(grid, board_c, fwd)
    switch = ev._flip_near(grid, zero_c, fwd)
    am_today = (fr.expiry, True) in sms and bdt.time() < time(9, 30)
    after_open = None
    if am_today:
        keep = {k: v for k, v in sms.items() if k != (fr.expiry, True)}
        after_open = ev._flip_near(grid, cb.gamma_curve(keep, grid), fwd)
    gam = {k: round(float(np.interp(fwd * m, grid, board_c)), 3)
           for k, m in (("at_centre", 1.0), ("down_1pct", 0.99), ("up_1pct", 1.01))}

    band = max(3.0 * strd, 0.02 * fwd)      # the 80% range plus the next big level beyond it
    hi_day = date.fromordinal(floor.toordinal() + OI_DAYS)
    lv = pick_levels(_oi_by_strike(sms, floor, hi_day), fwd, band, 0.5 * strd)
    lo_e = (lv.get("floor") or {}).get("strike") or round(fwd - strd)
    ups = [x for x in (switch, board) if x is not None and x > fwd + 5]
    hi_e = min(ups) if ups else ((lv.get("cap") or {}).get("strike") or round(fwd + strd))
    p3 = ev._buckets(g_s, surv, [float(lo_e), float(hi_e)])

    # overnight: previous morning vs this one (same 08:35 time of day -> a fair comparison)
    prev_books = cb.recent_books(session, sym, ts, HISTORY_BOOKS + 1)
    overnight: dict[str, Any] = {"gap_pts": round(fwd - prior, 1)}
    if prev_books:
        prev_rows = cb.book_rows(session, sym, prev_books[0], max_days=60)
        pk = book_spot(session, sym, prev_books[0])
        psm = cb.build_smiles(prev_rows, cb.book_time(pk["kind"] if pk else kind, prev_books[0].date()))
        pfr = cb.front(psm, prev_books[0].date())
        p_strd = cb.straddle_mid(pfr)[0] if pfr else None
        overnight["prev_straddle"] = round(p_strd, 1) if p_strd else None
        overnight["gap_x_prev_move"] = round((fwd - prior) / p_strd, 2) if p_strd else None
        hist = cb.vol_history(session, sym, prev_books, kind, {b.date(): fwd for b in prev_books})
        overnight["vol"] = vol_change((cb.cm_atm_iv(sms, 7), cb.cm_atm_iv(sms, 30)), hist)
        overnight["oi"] = oi_overnight(stored, prev_rows, day, fwd, 2 * strd, hi_day)
        overnight["prev_book_day"] = prev_books[0].date().isoformat()
        overnight["vol_since_close"] = _since_close(session, sym, prev_books[0].date(), cb.cm_atm_iv(sms, 30))
    late = _latest_spot(session, sym)
    latest = {"spot": round(late[0], 2), "ts": late[1].isoformat(timespec="minutes"),
              "after_book": late[1] > bdt} if late else None

    stamp = f"{label} (prices {bdt:%a %m/%d %H:%M})"
    return {
        "symbol": sym, "found": True, "session": fr.expiry.isoformat(), "book_day": day.isoformat(),
        "book_kind": kind, "label": label, "book_time": bdt.isoformat(timespec="minutes"),
        "centre": round(fwd, 2), "forward": round(fwd, 2), "prior_close": round(prior, 2),
        "prior_close_ts": bs["spot_ts"], "latest": latest,
        "regime": regime(fwd, switch, board),
        "switch_0dte": round(switch, 1) if switch else None, "board_switch": round(board, 1) if board else None,
        "board_switch_after_open": round(after_open, 1) if after_open else None,
        "am_settled_today": am_today, "gamma": gam, "dealer_sign": DEALER_SIGN,
        "levels": lv, "straddle": round(strd, 2), "straddle_strike": strd_k, "atm_iv": round(atm, 4),
        "one_sigma": [round(fwd - fwd * atm * math.sqrt(fr.t), 1), round(fwd + fwd * atm * math.sqrt(fr.t), 1)],
        "ranges": ranges,
        "three_ways": {"edges": [lo_e, round(hi_e, 1)], "break": round(100 * p3[0], 1),
                       "range": round(100 * p3[1], 1), "pop": round(100 * p3[2], 1)},
        "odds_quality": quality, "odds_reasons": why, "overnight": overnight,
        "if_then": if_then(fwd, lv, switch, board, ranges["80%"], fr.expiry.strftime("%A")),
        "checks": {"one_centre": True, "forward_spread_pts": round(fr.parity_spread or 0.0, 2),
                   "oi_updated": (overnight.get("oi") or {}).get("updated"),
                   "front_is_today": fr.expiry == bdt.date(), "smile_points": int(len(fr.strikes))},
        "sources": {
            "forward": f"{stamp}: call minus put mid prices at the 5 strikes nearest the money (put-call parity)",
            "prior_close": f"greeks_snapshots, last SPX spot before the book ({bs['spot_ts']})",
            "straddle": f"real mid prices, call + put at {strd_k:,.0f} ({fr.expiry})" if strd_k else "model",
            "odds_ranges": f"our IVs from {stamp} mid prices, front expiry {fr.expiry}, smoothed smile",
            "switches": f"gamma from our IVs and the book's open interest, centred on the forward ({DEALER_SIGN})",
            "levels": f"open interest in {stamp} (posted overnight = positions at the prior close), expiries "
                      f"{floor} to {hi_day}, within {band:,.0f} pts",
            "overnight": "this morning's book vs the previous morning's (same 08:35 time), our own IVs",
            "one_sigma": "our arithmetic: forward x ATM IV x sqrt(time to settlement)",
        },
        "note": "descriptor, not a signal; risk-neutral odds are not a forecast",
    }
