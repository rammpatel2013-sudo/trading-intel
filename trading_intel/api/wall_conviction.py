"""Wall-conviction + rolling net-GEX reader for the ticker report (and MCP).

Reads banked tables only (``oi_chain_eod``, ``greeks_snapshots``,
``earnings_events``) — zero vendor calls. Fixed-strike vol comes straight from the
per-strike IV banked in ``oi_chain_eod`` (tracking ONE expiry at the wall strike),
so no option-price reconstruction is needed. The scoring is the pure
``market.wall_conviction``; this module only gathers inputs and renders a
server-side static-SVG block (phone-safe). Descriptor only (rule 4).
"""
from __future__ import annotations

import html
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.market import wall_conviction as wc
from trading_intel.memory.models import EarningsEvent, GreeksSnapshot, OiChainEod

log = structlog.get_logger(__name__)

_DTE_MAX = 60
# dte >= 1: the AM index book now carries today's expiry (dte 0), which rolls every day
# and would make the multi-day 'level stable' test meaningless.
_DTE_MIN = 1
_HIST_SNAPS = 15


def _day(ts: Any) -> date:
    return ts.date() if isinstance(ts, datetime) else ts


def _walls(session: Session, sym: str) -> tuple[list[datetime], dict[str, list[float | None]]]:
    """Per-EOD-snapshot call/put wall (max summed gxoi strike, <=60 DTE)."""
    ts_rows = session.execute(
        select(OiChainEod.ts).where(OiChainEod.symbol == sym)
        .group_by(OiChainEod.ts).order_by(OiChainEod.ts.desc()).limit(_HIST_SNAPS)
    ).scalars().all()
    stamps = sorted(ts_rows)
    walls: dict[str, list[float | None]] = {"call": [], "put": []}
    if not stamps:
        return [], walls
    rows = session.execute(
        select(OiChainEod.ts, OiChainEod.cp, OiChainEod.strike, func.sum(OiChainEod.gxoi))
        .where(OiChainEod.symbol == sym, OiChainEod.ts.in_(stamps),
               OiChainEod.dte.between(_DTE_MIN, _DTE_MAX))
        .group_by(OiChainEod.ts, OiChainEod.cp, OiChainEod.strike)
    ).all()
    best: dict[tuple, tuple[float, float]] = {}
    for ts, cp, k, g in rows:
        side = "call" if str(cp).upper().startswith("C") else "put"
        g = abs(g or 0.0)
        if g > best.get((ts, side), (-1.0, 0.0))[0]:
            best[(ts, side)] = (g, k)
    for ts in stamps:
        for side in ("call", "put"):
            walls[side].append(best.get((ts, side), (0, None))[1])
    return stamps, walls


def _fixed_strike(session: Session, sym: str, strike: float, cp: str,
                  stamps: list[datetime]) -> dict[str, Any]:
    """IV at ONE (strike, expiry) over time + today's liquidity at the wall."""
    rows = session.execute(
        select(OiChainEod.ts, OiChainEod.expiry, OiChainEod.iv, OiChainEod.oi, OiChainEod.volume)
        .where(OiChainEod.symbol == sym, OiChainEod.strike == strike, OiChainEod.cp == cp,
               OiChainEod.ts.in_(stamps), OiChainEod.dte.between(_DTE_MIN, _DTE_MAX))
    ).all()
    if not rows:
        return {"expiry": None, "series": [], "oi": None, "volume": None}
    last = max(r[0] for r in rows)
    today = [r for r in rows if r[0] == last]
    track = max(today, key=lambda r: r[3] or 0)[1]
    by_ts = {r[0]: r[2] for r in rows if r[1] == track}
    series = [(_day(t), by_ts.get(t)) for t in stamps if t in by_ts]
    return {
        "expiry": track,
        "series": series,
        "oi": sum(r[3] or 0 for r in today),
        "volume": sum(r[4] or 0 for r in today),
    }


def _chain_oi(session: Session, sym: str, ts: datetime, cp: str) -> list[float]:
    rows = session.execute(
        select(func.sum(OiChainEod.oi)).where(
            OiChainEod.symbol == sym, OiChainEod.ts == ts, OiChainEod.cp == cp,
            OiChainEod.dte.between(_DTE_MIN, _DTE_MAX))
        .group_by(OiChainEod.strike)
    ).scalars().all()
    return [float(x) for x in rows if x is not None]


def net_gex_series(session: Session, sym: str, *, days: int = 180,
                   iv_max: float = 2.0) -> list[dict[str, Any]]:
    """Last greeks_snapshot per day: net GEX + a stale flag (bad IV / frozen value)."""
    rows = session.execute(
        select(GreeksSnapshot.ts, GreeksSnapshot.gex_total, GreeksSnapshot.atm_iv, GreeksSnapshot.spot)
        .where(GreeksSnapshot.symbol == sym).order_by(GreeksSnapshot.ts)
    ).all()
    per_day: dict[date, tuple] = {}
    for ts, g, iv, sp in rows:
        per_day[_day(ts)] = (g, iv, sp)
    out: list[dict[str, Any]] = []
    prev = None
    for d in sorted(per_day)[-days:]:
        g, iv, sp = per_day[d]
        stale = g is None or (iv is not None and iv > iv_max) or (prev is not None and g == prev)
        out.append({"date": d, "gex": g, "spot": sp, "stale": bool(stale)})
        prev = g
    return out


def build_wall_conviction(session: Session, symbol: str, *, today: date | None = None) -> dict[str, Any]:
    sym = symbol.strip().upper()
    today = today or date.today()
    stamps, walls = _walls(session, sym)
    spot_row = session.execute(
        select(GreeksSnapshot.spot).where(GreeksSnapshot.symbol == sym, GreeksSnapshot.spot.isnot(None))
        .order_by(GreeksSnapshot.ts.desc()).limit(1)
    ).scalar_one_or_none()
    ev = session.execute(
        select(EarningsEvent.date).where(EarningsEvent.symbol == sym, EarningsEvent.date >= today)
    ).scalars().all()
    e_days = wc.earnings_days(today, list(ev))
    out: dict[str, Any] = {"symbol": sym, "spot": spot_row, "earnings_in_days": e_days,
                           "as_of": _day(stamps[-1]) if stamps else None, "walls": {}}
    for side, cp in (("call", "C"), ("put", "P")):
        hist = walls.get(side) or []
        strike = next((w for w in reversed(hist) if w is not None), None)
        fs = _fixed_strike(session, sym, strike, cp, stamps) if strike is not None else {
            "expiry": None, "series": [], "oi": None, "volume": None}
        rank = wc.oi_rank(fs["oi"], _chain_oi(session, sym, stamps[-1], cp)) if stamps and strike else None
        read = wc.read_wall(side, spot=spot_row, wall_history=hist,
                            fsv_series=[v for _, v in fs["series"]],
                            earnings_in_days=e_days, oi_rank_value=rank, wall_volume=fs["volume"])
        out["walls"][side] = {**read.as_dict(), "history": hist, "fsv": fs["series"],
                              "fsv_expiry": fs["expiry"], "oi_rank": rank,
                              "wall_oi": fs["oi"], "wall_volume": fs["volume"]}
    out["gex_series"] = net_gex_series(session, sym)
    return out


# ── rendering (server-side static SVG; no <script>, phone-safe) ─────────────
_VCOL = {"HOLD": "#3fb950", "BREAK": "#f85149", "WAIT": "#e3b341", "NO READ": "#8b949e"}


def _line_svg(points: list[tuple[int, float | None, bool]], *, w: int = 540, h: int = 120,
              zero: bool = False, col: str = "#58a6ff") -> str:
    """points = (x_index, y, stale). Solid runs for fresh, dashed amber for stale."""
    ys = [y for _, y, _ in points if y is not None]
    if len(ys) < 2:
        return "<p class='muted'>not enough history</p>"
    lo, hi = min(ys + ([0.0] if zero else [])), max(ys + ([0.0] if zero else []))
    rng = (hi - lo) or 1.0
    n = max(p[0] for p in points) or 1

    def xy(i: int, y: float) -> str:
        return f"{8 + i / n * (w - 16):.0f},{h - 6 - (y - lo) / rng * (h - 12):.0f}"

    segs, cur, cur_stale = [], [], None
    for i, y, st in points:
        if y is None:
            continue
        if cur_stale is not None and st != cur_stale and cur:
            segs.append((cur_stale, cur))
            cur = [cur[-1]]
        cur.append(xy(i, y))
        cur_stale = st
    if cur:
        segs.append((cur_stale, cur))
    dash = ' stroke-dasharray="4 3"'
    lines = "".join(
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{"#e3b341" if st else col}" '
        f'stroke-width="1.6"{dash if st else ""}/>'
        for st, pts in segs if len(pts) > 1)
    zl = ""
    if zero and lo < 0 < hi:
        zy = h - 6 - (0 - lo) / rng * (h - 12)
        zl = f'<line x1="0" x2="{w}" y1="{zy:.0f}" y2="{zy:.0f}" stroke="#555" stroke-width="0.8"/>'
    lab = (f'<text x="4" y="11" font-size="10" fill="#8b949e">{hi:,.0f}</text>'
           f'<text x="4" y="{h - 2}" font-size="10" fill="#8b949e">{lo:,.0f}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}">{zl}{lines}{lab}</svg>'


def _fmt(x: float | None, dp: int = 1, suf: str = "") -> str:
    return "—" if x is None else f"{x:,.{dp}f}{suf}"


def _wall_card(side: str, d: dict[str, Any]) -> str:
    v = d.get("verdict", "NO READ")
    col = _VCOL.get(v, "#8b949e")
    chk = lambda ok: "✅" if ok else ("—" if ok is None else "❌")
    fsv = d.get("fsv") or []
    spark = _line_svg([(i, (iv * 100 if iv else None), False) for i, (_, iv) in enumerate(fsv)],
                      w=260, h=60, col="#bc8cff")
    reasons = "; ".join(html.escape(r) for r in d.get("reasons") or [])
    thin = " · <span style='color:#e3b341'>thin strike</span>" if d.get("thin") else ""
    return (
        f'<div class="card" style="border-left:3px solid {col}"><h3>{side.title()} wall '
        f'{_fmt(d.get("strike"), 0)} — <span style="color:{col}">{v}</span></h3><table>'
        f'<tr><th>1 · level stable</th><td>{chk(d.get("stable"))}</td></tr>'
        f'<tr><th>2 · spot at level</th><td>{chk(d.get("proximity") == "at")} '
        f'{html.escape(str(d.get("proximity") or ""))} ({_fmt((d.get("dist_pct") or 0) * 100, 1, "%")})</td></tr>'
        f'<tr><th>3 · fixed-strike vol</th><td>{html.escape(str(d.get("fsv_state")))} '
        f'({_fmt(d.get("fsv_change_pts"), 1, " pts")})</td></tr>'
        f'<tr><th>liquidity</th><td>OI rank {_fmt((d.get("oi_rank") or 0) * 100, 0, "%")} · '
        f'vol {_fmt(d.get("wall_volume"), 0)}{thin}</td></tr></table>'
        f'<div class="note">IV at {_fmt(d.get("strike"), 0)} · exp {d.get("fsv_expiry") or "—"}</div>{spark}'
        f'<p class="note">{reasons}</p></div>'
    )


def render_section(d: dict[str, Any]) -> str:
    if not d.get("walls"):
        return ""
    e = d.get("earnings_in_days")
    e_txt = f" · earnings in {e}d" if e is not None else ""
    cards = _wall_card("call", d["walls"]["call"]) + _wall_card("put", d["walls"]["put"])
    gs = d.get("gex_series") or []
    gsvg = _line_svg([(i, (r["gex"] / 1e6 if r["gex"] is not None else None), r["stale"])
                      for i, r in enumerate(gs)], w=1100, h=170, zero=True)
    span = f"{gs[0]['date']} → {gs[-1]['date']}" if gs else ""
    return (
        f'<h2 class="sec">Wall conviction — does the level HOLD? <span class="muted">'
        f'(as of {d.get("as_of")}{e_txt})</span></h2><div class="tw">{cards}</div>'
        '<p class="note">HOLD = stable wall + spot at it + fixed-strike vol calm/offered (dealers defend). '
        'BREAK = same but vol BID at the strike (dealers paying up, not defending). WAIT = earnings '
        'contaminates the vol read. Says hold-vs-break, never direction (rule 4).</p>'
        f'<h2 class="sec">Rolling net GEX ($mm) <span class="muted">{span} · zero line = long/short '
        'gamma flip · dashed amber = stale / bad-IV day</span></h2>' + gsvg
    )
