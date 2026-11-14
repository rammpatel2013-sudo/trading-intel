"""Daily-brief tape pillar: price board, bottom checklist, washout /6, follow-through day,
VIX-shock base rate and a posture line (2026-11-13 overnight; brief item 5, backlog #39 #41 #89,
Tao T1/T4/T5, Mithil's "Is it a bottom?" sheet).

Reads banked rows only (quotes_daily, vix_data, breadth_snapshots, the EOD option book via
get_profile) - no vendor calls. Every item that needs data we don't collect says "no data"
instead of guessing (spike / new-lows-500 need NYSE+Nasdaq new-high/new-low counts; AAII,
$DANC and CNBC are manual). Descriptive regime context only (rule 4).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.market import bottom_math as bmx
from trading_intel.memory.models import BreadthSnapshot, QuoteDaily, VixData

log = structlog.get_logger(__name__)
BOARD = ("SPX", "SPY", "QQQ", "IWM", "RSP", "SMH", "DIA")


def _bars(session: Session, sym: str, n: int = 400) -> list[QuoteDaily]:
    rows = session.execute(select(QuoteDaily).where(QuoteDaily.symbol == sym)
                           .order_by(QuoteDaily.date.desc()).limit(n)).scalars().all()
    return list(reversed(rows))


def price_board(session: Session) -> list[dict[str, Any]]:
    """#39: each index/ETF vs its 10 / 21 / 50 / 200-day averages."""
    out = []
    for sym in BOARD:
        b = _bars(session, sym, 260)
        if len(b) < 22:
            continue
        c = [float(x.close) for x in b]
        row = {"symbol": sym, "close": c[-1], "chg": 100 * (c[-1] / c[-2] - 1),
               "off_high": 100 * (c[-1] / max(c) - 1), "as_of": b[-1].date.isoformat()}
        for n in (10, 21, 50, 200):
            row[f"above_{n}"] = (c[-1] > sum(c[-n:]) / n) if len(c) >= n else None
        out.append(row)
    return out


def _vix(session: Session, n: int = 400) -> list[VixData]:
    rows = session.execute(select(VixData).where(VixData.vix.isnot(None))
                           .order_by(VixData.date.desc()).limit(n)).scalars().all()
    return list(reversed(rows))


def _breadth(session: Session, n: int = 15) -> list[BreadthSnapshot]:
    rows = session.execute(select(BreadthSnapshot).where(BreadthSnapshot.source == "fmp_sp500")
                           .order_by(BreadthSnapshot.ts.desc()).limit(n)).scalars().all()
    return list(reversed(rows))


def _box(item: str, ok: bool | None, detail: str) -> dict[str, Any]:
    return {"item": item, "state": "no data" if ok is None else ("yes" if ok else "no"), "detail": detail}


def _up_share(b: BreadthSnapshot) -> float | None:
    """Up-volume share when banked (from 11/13 on), else the advancing-issues share."""
    if b.up_volume and b.down_volume is not None and (b.up_volume + b.down_volume):
        return b.up_volume / (b.up_volume + b.down_volume)
    if b.advancers is not None and b.decliners is not None and (b.advancers + b.decliners):
        return b.advancers / (b.advancers + b.decliners)
    return None


def tape_context(session: Session) -> dict[str, Any]:
    """Everything the tape pillar shows. Each part fails soft (logged, left out)."""
    ctx: dict[str, Any] = {}
    spx, spy = _bars(session, "SPX"), _bars(session, "SPY")
    vix = _vix(session)
    br = _breadth(session)
    c = [float(x.close) for x in spx]
    vc = [float(v.vix) for v in vix]
    boxes: list[dict[str, Any]] = []
    try:
        osc = [o for o in bmx.vix_osc(vc)[-5:] if o is not None]
        peak = max(osc) if osc else None
        boxes.append(_box("VIX 1/10 oscillator in buy range", None if peak is None else peak >= 20,
                          f"peak {peak:+.0f}% vs 10-day avg in the last 5 sessions" if peak is not None else ""))
        if len(vc) >= 6:
            v = vix[-1]
            settle = vc[-1] <= 0.85 * max(vc[-6:]) and (v.vix9d or 0) <= (v.vix or 0) <= (v.vix3m or 99)
            boxes.append(_box("VIX settling / falling", settle,
                              f"VIX {vc[-1]:.1f} vs 5-day high {max(vc[-6:]):.1f}; 9D {v.vix9d or 0:.1f} / 3M {v.vix3m or 0:.1f}"))
        wk = bmx.elder(bmx.weekly([(x.date, float(x.close)) for x in spx]))
        boxes.append(_box("Weekly Elder at least blue", None if wk is None else wk != "red", f"weekly {wk}"))
        dy = bmx.elder(c)
        boxes.append(_box("Daily Elder not red", None if dy is None else dy != "red", f"daily {dy}"))
        h = bmx.macd(c)[2] if len(c) >= 40 else []
        boxes.append(_box("Daily MACD histogram rising", (h[-1] > h[-2]) if h else None,
                          f"{h[-2]:+.1f} -> {h[-1]:+.1f}" if h else ""))
        boxes.append(_box("Spike signal bounce", None, "needs NYSE+Nasdaq new-high/new-low counts (not collected)"))
        lows = br[-1].new_lows if br else None
        boxes.append(_box("New lows < 500", None, f"S&P 500 only: {lows} new lows" if lows is not None else ""))
        p = bmx.ppo(c) if len(c) >= 30 else []
        boxes.append(_box("SPX 12/26 PPO turning up", (p[-1] > p[-2]) if p else None,
                          f"{p[-2]:+.2f} -> {p[-1]:+.2f}" if p else ""))
        boxes.append(_box("$DANC trading model rolling over", None, "manual"))
        shares = [(b.ts, _up_share(b)) for b in br[-10:]]
        best = max((s for s in shares if s[1] is not None), key=lambda s: s[1], default=None)
        boxes.append(_box("80%+ up day (last 10 sessions)", None if best is None else best[1] >= 0.80,
                          f"best {best[1]:.0%} on {best[0]:%m/%d}" if best else ""))
        boxes.append(_box("AAII sentiment = bearish", None, "manual (aaii.com, Thursdays)"))
        ftd = bmx.follow_through(c[-40:], [float(x.volume) for x in spy[-40:]] if len(spy) >= 40 else [None] * 40)
        ctx["ftd"] = ftd
        boxes.append(_box("Rally attempt", ftd["state"] in ("rally attempt", "follow-through"),
                          f"day {ftd.get('attempt_day')}" if ftd.get("attempt_day") else ftd["state"]))
        fd = ftd.get("ftd_idx")
        boxes.append(_box("Follow-through day", fd is not None,
                          (f"{spx[-40:][fd].date:%m/%d}; gave back {ftd['ftd_gave_back']:.0%} since"
                           if fd is not None and ftd.get("ftd_gave_back") is not None else "")))
        boxes.append(_box("CNBC 'Markets in Turmoil' (bonus)", None, "manual"))
        boxes.append(_box("Super spike buy signal", None, "needs NYSE+Nasdaq new-high/new-low counts"))
    except Exception:  # noqa: BLE001 - the brief must render without this pillar
        log.warning("brief_tape.checklist_failed", exc_info=True)
    ctx["checklist"] = boxes
    ctx["checked"] = sum(b["state"] == "yes" for b in boxes)
    ctx["checkable"] = sum(b["state"] != "no data" for b in boxes)
    try:
        if len(spx) >= 2 and br:
            last = spx[-1]
            ctx["washout"] = bmx.washout(
                spy_vol=[float(x.volume) for x in spy[-30:]], up_share=[_up_share(b) for b in br],
                spx_lows=[float(x.low) for x in spx[-15:]], vix_closes=vc[-15:],
                open_=float(last.open), low=float(last.low), close=float(last.close),
                prev_close=float(spx[-2].close), pct50=[b.pct_above_50 for b in br])
    except Exception:  # noqa: BLE001
        log.warning("brief_tape.washout_failed", exc_info=True)
    try:
        by_d = {x.date: float(x.close) for x in spx}
        pairs = [(v.date, float(v.vix), by_d[v.date]) for v in vix if v.date in by_d]
        if pairs:
            ctx["shock"] = bmx.shock_base_rate([p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs])
    except Exception:  # noqa: BLE001
        log.warning("brief_tape.shock_failed", exc_info=True)
    try:
        ctx["board"] = price_board(session)
    except Exception:  # noqa: BLE001
        log.warning("brief_tape.board_failed", exc_info=True)
    if br:
        b = br[-1]
        ctx["stockbee"] = {k: getattr(b, k) for k in ("up4", "down4", "up25q", "down25q", "pct_above_40",
                                                      "setups_base", "setups_pullback", "pct_above_50",
                                                      "new_highs", "new_lows")}
        ctx["stockbee"]["up_share"] = _up_share(b)
        ctx["stockbee"]["as_of"] = b.ts.isoformat()
    ctx["posture"] = posture(ctx)
    return ctx


def posture(ctx: dict[str, Any]) -> dict[str, Any]:
    """#41 one line: press / selective / stand aside, with the reasons that set it."""
    pts, why = 0, []
    board = {r["symbol"]: r for r in ctx.get("board") or []}
    s = board.get("SPX") or board.get("SPY")
    if s:
        if s.get("above_21") and s.get("above_50"):
            pts += 1; why.append("SPX above its 21 and 50-day")
        elif s.get("above_21") is False and s.get("above_50") is False:
            pts -= 1; why.append("SPX below its 21 and 50-day")
    sb = ctx.get("stockbee") or {}
    if sb.get("up4") is not None and sb.get("down4") is not None:
        if sb["up4"] > 2 * max(sb["down4"], 1):
            pts += 1; why.append(f"{sb['up4']} names up 4%+ vs {sb['down4']} down")
        elif sb["down4"] > 2 * max(sb["up4"], 1):
            pts -= 1; why.append(f"{sb['down4']} names down 4%+ vs {sb['up4']} up")
    ftd = ctx.get("ftd") or {}
    if ftd.get("state") == "follow-through" and (ftd.get("ftd_gave_back") or 0) < 1:
        pts += 1; why.append("follow-through day holding")
    if sb.get("pct_above_50") is not None and sb["pct_above_50"] < 30:
        pts -= 1; why.append(f"only {sb['pct_above_50']}% above their 50-day")
    word = "press" if pts >= 2 else ("stand aside" if pts <= -1 else "selective")
    return {"word": word, "score": pts, "why": why}


def full_book_regime(session: Session, symbol: str = "SPX") -> dict[str, Any] | None:
    """Tao T1: regime from the FULL EOD book (all expiries), not the front-3 snapshot."""
    try:
        from trading_intel.mcp.profile_tool import get_profile

        p = get_profile(session, symbol)
        g = ((p.get("profiles") or {}).get("gamma") or {})
        ref = (p.get("profiles") or {}).get("spot_ref") or []
        vals = g.get("all") or []
        if not p.get("found") or not vals or not ref:
            return None
        spot = p["spot"]
        i = min(range(len(ref)), key=lambda k: abs(ref[k] - spot))
        net = vals[i]
        return {"as_of": p.get("as_of"), "spot": spot, "net_gamma": net, "flip": g.get("flip"),
                "label": "long gamma" if net > 0 else "short gamma"}
    except Exception:  # noqa: BLE001
        log.warning("brief_tape.full_book_failed", exc_info=True)
        return None
