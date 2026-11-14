"""Desk brief - turn one session's rows into the brief (2026-11-14). ``build`` is pure.

Order follows TailThatWagsDog's 11/13 brief: screen -> scoreboard -> inverse / ETF prints ->
leaders -> dark-pool + options confirmation (two-leg) -> divergence lists -> cross-currents ->
"what matters" + desk read (rule-written sentences, no AI - rule 7) -> top pick.
Descriptor only (rule 4): nothing here writes to ``signals``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from trading_intel.desk import metrics as m
from trading_intel.desk import store

D1_MIN, D5_MAX = 0.5, 0.0  # screen: up > 0.5% today after a down 5 sessions
PRICE_MIN, DVOL_MIN = 5.0, 10e6  # universe floor: $5 and $10M average dollar volume
# his scan's other filters are not published; fitted to his 11/13 list (keeps 55 of his 57 names, 104 total
# vs 216 without them): close in the upper half of the day's range, price >= $20, day $ volume >= $30M.
BSR_MIN, SCREEN_PRICE_MIN, SCREEN_DVOL_MIN = 1.0, 20.0, 30e6
BACKDROP = ("SPY", "QQQ", "IWM", "SMH", "RSP")


# ── gather (DB -> one dict per symbol) ──────────────────────────────────
def gather(session: Session, day: date, *, universe: Sequence[str] | None = None) -> dict[str, Any]:
    """Everything ``build`` needs for ``day``. Coverage flags say which legs are missing."""
    names = store.load_names(session)
    und_days = store.und_dates(session, day, 1)
    und = store.load_und(session, und_days[0]) if und_days else {}
    syms = list(universe) if universe is not None else sorted(set(und) | set(BACKDROP))
    bars = store.load_bars(session, syms, day - timedelta(days=40), day)
    dates, finra = store.load_finra(session, day, syms)
    gamma = store.load_gamma(session, day)
    rows: dict[str, dict] = {}
    for s in syms:
        b = [x for x in bars.get(s, []) if x[4]]
        if len(b) < 6 or b[-1][0] != day:
            continue
        closes = [x[4] for x in b]
        d1, d5 = m.changes(closes)
        _, _o, h, lo, c, v = b[-1]
        nm = names.get(s, {})
        dv20 = m.avg([x[4] * (x[5] or 0) for x in b[-20:]])
        u = und.get(s)
        g = gamma.get(s)
        dark = m.dark_windows(finra.get(s, [])) if dates and dates[0] == day else m.dark_windows([])
        rows[s] = {
            "symbol": s,
            "name": nm.get("name"),
            "industry": nm.get("industry"),
            "group": m.group_for(nm.get("sector"), nm.get("industry")),
            "is_etf": bool(nm.get("is_etf")),
            "close": c,
            "high": h,
            "low": lo,
            "volume": v,
            "d1": d1,
            "d5": d5,
            "bsr": m.bsr(h, lo, c),
            "dvol": c * (v or 0),
            "dvol20": dv20,
            "dark": dark,
            "opt_volume": getattr(u, "option_volume", None),
            "bull": (
                m.bullish_pct(
                    *(getattr(u, k, None) for k in ("call_buy", "call_sell", "put_buy", "put_sell"))
                )
                if u
                else None
            ),
            "st_gex": getattr(g, "st_gex", None),
            "total_gex": getattr(g, "total_gex", None),
            "lev": m.leverage(s, nm.get("name")) if nm.get("is_etf") else None,
        }
        rows[s]["gex_vol"] = m.gex_vol(rows[s]["st_gex"], rows[s]["dvol"])
    cov = {
        "finra": bool(dates and dates[0] == day),
        "finra_date": str(dates[0]) if dates else None,
        "options": any(r["bull"] is not None for r in rows.values()),
        "und_date": str(und_days[0]) if und_days else None,
        "gamma": bool(gamma),
        "n_universe": len(rows),
    }
    return {"day": day, "rows": rows, "coverage": cov}


# ── build (pure) ────────────────────────────────────────────────────────
def _liquid(r: Mapping) -> bool:
    return (r["close"] or 0) >= PRICE_MIN and (r.get("dvol20") or 0) >= DVOL_MIN


def _his_filters(r: Mapping) -> bool:
    return (
        (r.get("bsr") or 0) >= BSR_MIN
        and (r["close"] or 0) >= SCREEN_PRICE_MIN
        and (r.get("dvol") or 0) >= SCREEN_DVOL_MIN
    )


def _lite(r: Mapping) -> dict:
    keep = (
        "symbol",
        "name",
        "group",
        "industry",
        "d1",
        "d5",
        "bsr",
        "close",
        "high",
        "low",
        "volume",
        "dvol",
        "bull",
        "st_gex",
        "gex_vol",
    )
    out = {k: r.get(k) for k in keep}
    out["dark"] = dict(r.get("dark") or {})
    return out


def _f(x: float | None, d: int = 2, pct: bool = True) -> str:
    return "n/a" if x is None else (f"{x:+.{d}f}%" if pct else f"{x:.{d}f}")


def build(g: Mapping[str, Any]) -> dict[str, Any]:
    rows: dict[str, dict] = g["rows"]
    day: date = g["day"]
    cov = dict(g["coverage"])
    hit = [
        r
        for r in rows.values()
        if r["d1"] is not None
        and r["d5"] is not None
        and r["d1"] > D1_MIN
        and r["d5"] < D5_MAX
        and _liquid(r)
    ]
    screen = sorted((r for r in hit if not r["is_etf"] and _his_filters(r)), key=lambda r: -r["d1"])
    etfs = sorted((r for r in hit if r["is_etf"]), key=lambda r: -(r["volume"] or 0))
    cov["n_screen"] = len(screen)

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in screen:
        groups[r["group"]].append(r)
    board = []
    for grp, rs in groups.items():
        d1s = [r["d1"] for r in rs]
        board.append(
            {
                "group": grp,
                "n": len(rs),
                "avg1": m.avg(d1s),
                "med1": m.med(d1s),
                "n2": sum(1 for x in d1s if x > 2),
                "avg5": m.avg([r["d5"] for r in rs]),
                "med_bsr": m.med([r["bsr"] for r in rs if r["bsr"] is not None]),
                "names": [(r["symbol"], round(r["d1"], 2)) for r in rs],
            }
        )
    board.sort(key=lambda b: -(b["avg1"] or 0))
    totals = {
        "n": len(screen),
        "avg1": m.avg([r["d1"] for r in screen]),
        "med1": m.med([r["d1"] for r in screen]),
        "n2": sum(1 for r in screen if r["d1"] > 2),
        "avg5": m.avg([r["d5"] for r in screen]),
        "med_bsr": m.med([r["bsr"] for r in screen if r["bsr"] is not None]),
    }

    prints = [
        {
            "symbol": r["symbol"],
            "name": r["name"],
            "d1": r["d1"],
            "d5": r["d5"],
            "volume": r["volume"],
            "lev": r["lev"],
            "implied": (r["d1"] / r["lev"]) if r["lev"] else None,
        }
        for r in etfs[:10]
    ]

    def leg_opts(r: Mapping) -> bool | None:
        return None if r["bull"] is None else r["bull"] > m.BULLISH_MIN

    two = [r for r in screen if r["dark"]["pass"] and leg_opts(r)]
    div_a = [r for r in screen if r["dark"]["pass"] and leg_opts(r) is False]
    div_b = [
        r for r in screen if leg_opts(r) and not r["dark"]["pass"] and r["dark"]["w1"] is not None
    ]
    dark_only = [r for r in screen if r["dark"]["pass"]] if not cov["options"] else []

    by_ind: dict[str, list[dict]] = defaultdict(list)
    for r in screen:
        if r["industry"]:
            by_ind[r["industry"]].append(r)
    cross = sorted(
        (
            {
                "industry": k,
                "n": len(v),
                "avg1": m.avg([r["d1"] for r in v]),
                "avg5": m.avg([r["d5"] for r in v]),
                "avg_bsr": m.avg([r["bsr"] for r in v if r["bsr"] is not None]),
                "names": [r["symbol"] for r in v],
            }
            for k, v in by_ind.items()
            if len(v) >= 2
        ),
        key=lambda c: (-c["n"], -(c["avg1"] or 0)),
    )[:8]

    top = _top_pick(two)
    backdrop = {s: rows[s]["d1"] for s in BACKDROP if s in rows}
    return {
        "date": day.isoformat(),
        "coverage": cov,
        "totals": totals,
        "backdrop": backdrop,
        "screen": [_lite(r) for r in screen],
        "scoreboard": board,
        "etf_prints": prints,
        "leaders": [_lite(r) for r in screen[:12]],
        "two_leg": [_lite(r) for r in two],
        "div_a": [r["symbol"] for r in div_a],
        "div_b": [r["symbol"] for r in div_b],
        "dark_only": [_lite(r) for r in dark_only],
        "cross": cross,
        "top_pick": top,
        "matters": _matters(board, prints, backdrop),
        "desk_read": _desk_read(screen, board, two, prints),
    }


def _top_pick(two: Sequence[Mapping]) -> dict | None:
    ok = [r for r in two if (r["st_gex"] or 0) > 0 and abs(r["gex_vol"] or 0) >= m.GEX_VOL_MIN]
    if not ok:
        return None
    best = max(ok, key=lambda r: r["gex_vol"])
    why = (
        "the only two-leg name with positive short-term gamma and real options activity"
        if len(ok) == 1
        else f"highest gamma-to-volume of the {len(ok)} two-leg names with positive gamma"
    )
    out = _lite(best)
    out["why"] = why
    out["n_two_leg"] = len(two)
    return out


def _matters(board: Sequence[Mapping], prints: Sequence[Mapping], backdrop: Mapping) -> list[str]:
    out: list[str] = []
    if prints and prints[0]["implied"] is not None:
        p = prints[0]
        out.append(
            f"Backdrop: heaviest fund volume was {p['symbol']} ({(p['volume'] or 0) / 1e6:.0f}M shares, "
            f"{_f(p['d1'])}) - its underlying moved about {_f(p['implied'])}."
        )
    elif backdrop:
        out.append(
            "Backdrop: "
            + ", ".join(f"{k} {_f(v)}" for k, v in backdrop.items() if v is not None)
            + "."
        )
    big = [b for b in board if b["n"] >= 3] or [b for b in board if b["n"] >= 2] or list(board)
    if big:
        best = max(big, key=lambda b: b["med1"] or -99)
        out.append(
            f"Best breadth: {best['group']} - median {_f(best['med1'])}, "
            f"{best['n2']} of {best['n']} above +2%."
        )
        block = max(big, key=lambda b: (b["n2"] / b["n"], b["n"]))
        if block is not best and block["n2"]:
            out.append(
                f"Block move: {block['group']} - {block['n2']} of {block['n']} names above +2%."
            )
        trap = max(big, key=lambda b: (b["avg1"] or 0) - (b["med1"] or 0))
        gap = (trap["avg1"] or 0) - (trap["med1"] or 0)
        if gap >= 0.5:
            lift = ", ".join(s for s, _ in sorted(trap["names"], key=lambda x: -x[1])[:2])
            out.append(
                f"Average trap: {trap['group']} averages {_f(trap['avg1'])} but its median is "
                f"{_f(trap['med1'])} - lifted by {lift}."
            )
    small = [b for b in board if b["n"] >= 2]
    small = small if len(small) >= 2 else list(board)
    if len(small) >= 2:
        worst = min(small, key=lambda b: b["med1"] or 99)
        out.append(f"Laggard: {worst['group']} - median {_f(worst['med1'])}.")
    return out[:5]


def _desk_read(
    screen: Sequence[Mapping],
    board: Sequence[Mapping],
    two: Sequence[Mapping],
    prints: Sequence[Mapping],
) -> list[str]:
    if not screen:
        return ["Nothing passed the screen today - no oversold names bounced with size."]
    shallow = [r for r in screen if r["d5"] > -3]
    deep = sorted((r for r in screen if r["d5"] <= -10), key=lambda r: r["d5"])
    out = [
        f"{len(shallow)} of {len(screen)} names came off shallow 5-day dips (better than -3%) - that is "
        f"rotation, which tends to stick. {len(deep)} came off deep drops (-10% or worse)"
        + (f" ({', '.join(r['symbol'] for r in deep[:3])})" if deep else "")
        + " - reflex bounces; they need a close above today's high next session."
    ]
    absorbed = [r["symbol"] for r in screen if (r["bsr"] or 0) >= 20 and r["d1"] < 1]
    thin = [
        r["symbol"] for r in screen if r["d1"] >= 1.5 and r["bsr"] is not None and r["bsr"] < 1.5
    ]
    if absorbed or thin:
        out.append(
            (
                "Heavy buying absorbed (closed near the high, small gain): "
                + ", ".join(absorbed[:5])
                + ". "
                if absorbed
                else ""
            )
            + (
                "Moved on thin buying (closed mid-range): " + ", ".join(thin[:5]) + "."
                if thin
                else ""
            )
        )
    lead = max((b for b in board if b["n"] >= 3), key=lambda b: b["med1"] or -99, default=None)
    p0 = prints[0] if prints else None
    comp = (
        f"what {p0['symbol']} is short" if p0 and (p0.get("lev") or 0) < 0 else "the sold complex"
    )
    out.append(
        "Confirms next session if "
        + (f"{lead['group']} holds its breadth" if lead else "breadth holds")
        + (
            f" and the {len(two)} two-leg names keep dark ratio above 1 and options above 50% bullish"
            if two
            else ""
        )
        + f"; weakens if {comp} rebounds and draws the money back."
    )
    return out
