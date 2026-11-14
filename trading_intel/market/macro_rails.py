# ruff: noqa: E501
"""Macro rails (#60 group, 2026-11-13 overnight): credit/MOVE spark, VIX futures curve rail, event gate.

Pure logic, unit-tested (tests/market/test_macro_rails.py); ``scripts/macro_rails.py`` feeds it.
Ideas from the TTWD and GammaGirlLab digests (``claude/tailthatwagsdog-methodology.md`` #1 #2,
``claude/gammagirllab-methodology.md`` G7). Descriptors only (rule 4): states and context, no trades.

* Credit spark: HY OAS (FRED BAMLH0A0HYM2, in %) up >= 13 bp in a day = "spark"; non-confirmation
  = SPY or QQQ closes at a 20-day high while HY OAS is NOT at its 20-day low; HYG/IEI below its
  20-day average = credit not confirming (the "veto"). MOVE (bond VIX) 1-day and 20-day change.
* VIX futures curve rail: VX1/VX2/VX3 = the three nearest MONTHLY VIX expiries' forwards, taken
  from the VIX option chain we already collect (put-call parity forward = the matching VX future to
  within cents; no new vendor). VX2 - VX1 >= +0.75 normal contango, 0..+0.75 flattening, < 0
  backwardation; VX3 - VX1 < 0 = the whole front inverted.
* Event gate: each event in the next sessions with its own history (mean |SPX close-to-close| on
  past days of that kind vs all days) and the bond-vol percentile -> GREEN / YELLOW / RED with a
  suggested size factor for short-premium trades (1 / 0.75 / 0.5).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

SPARK_BP = 13.0
VX_CONTANGO = 0.75


# ---------------------------------------------------------------- credit / MOVE
def credit_spark(
    hy: list[tuple[date, float]],
    spy: list[tuple[date, float]],
    qqq: list[tuple[date, float]],
    hyg_iei: list[tuple[date, float]],
    move: list[tuple[date, float]],
) -> dict:
    """Each series = [(date, value)] oldest first (HY OAS in %, the rest prices / ratios)."""
    out: dict = {"found": False}
    if len(hy) < 21:
        out["reason"] = "HY OAS history too short"
        return out
    hv = [v for _, v in hy]
    d_bp = round((hv[-1] - hv[-2]) * 100.0, 1)
    low20 = min(hv[-20:])
    out.update(
        found=True,
        hy_date=hy[-1][0].isoformat(),
        hy_oas=round(hv[-1], 2),
        hy_chg_bp=d_bp,
        spark=d_bp >= SPARK_BP,
        hy_at_20d_low=hv[-1] <= low20 + 1e-9,
    )

    def at_high(s: list[tuple[date, float]]) -> bool | None:
        if len(s) < 20:
            return None
        v = [x for _, x in s]
        return v[-1] >= max(v[-20:]) - 1e-9

    hi = [x for x in (at_high(spy), at_high(qqq)) if x is not None]
    out["equity_20d_high"] = any(hi) if hi else None
    out["non_confirm"] = bool(out["equity_20d_high"]) and not out["hy_at_20d_low"]
    if len(hyg_iei) >= 20:
        r = [x for _, x in hyg_iei]
        avg = sum(r[-20:]) / 20.0
        out["hyg_iei"] = round(r[-1], 4)
        out["hyg_iei_vs_20d"] = round(100.0 * (r[-1] / avg - 1.0), 2)
        out["credit_veto"] = r[-1] < avg
    if len(move) >= 21:
        m = [x for _, x in move]
        out["move"] = round(m[-1], 1)
        out["move_chg1"] = round(m[-1] - m[-2], 1)
        out["move_chg20"] = round(m[-1] - m[-21], 1)
        yr = m[-252:]
        out["move_pct_1y"] = round(100.0 * sum(1 for x in yr if x <= m[-1]) / len(yr), 1)
    flags = []
    if out["spark"]:
        flags.append(f"credit SPARK: HY OAS +{d_bp:.0f} bp today")
    if out["non_confirm"]:
        flags.append("non-confirmation: stocks at a 20-day high, credit spreads not at their low")
    if out.get("credit_veto"):
        flags.append("HYG/IEI under its 20-day average (credit not confirming)")
    out["flags"] = flags
    out["state"] = (
        "red"
        if out["spark"]
        else "yellow" if (out["non_confirm"] or out.get("credit_veto")) else "green"
    )
    return out


# ---------------------------------------------------------------- VIX futures curve (from option forwards)
def third_friday(y: int, m: int) -> date:
    c = calendar.Calendar()
    fr = [d for d in c.itermonthdates(y, m) if d.month == m and d.weekday() == 4]
    return fr[2]


def is_vix_monthly(d: date) -> bool:
    """VIX monthly expiry = the Wednesday 30 days before the next month's 3rd Friday (holiday shifts ignored;
    a Tuesday one day early is accepted too)."""
    y, m = (d.year + (d.month // 12), d.month % 12 + 1)
    for yy, mm in (
        (y, m),
        (d.year, d.month),
    ):  # the expiry can sit in the month before or the same month
        t = third_friday(yy, mm) - timedelta(days=30)
        if d in (t, t - timedelta(days=1)):
            return True
    return False


def vx_curve(exps: list[dict], asof: date) -> dict:
    """exps = [{"e": "YYYY-MM-DD", "F": forward}] (vixmap_*.json). VX1 = first monthly not yet expired."""
    mon = []
    for x in exps or []:
        try:
            e = date.fromisoformat(str(x["e"])[:10])
            f = float(x["F"])
        except (KeyError, TypeError, ValueError):
            continue
        if e >= asof and f > 0 and is_vix_monthly(e):
            mon.append((e, f))
    mon.sort()
    if len(mon) < 2:
        return {"found": False, "reason": f"only {len(mon)} monthly VIX expiries with a forward"}
    v1, v2 = mon[0][1], mon[1][1]
    v3 = mon[2][1] if len(mon) > 2 else None
    s21 = round(v2 - v1, 2)
    s31 = round(v3 - v1, 2) if v3 is not None else None
    state = "backwardation" if s21 < 0 else "flattening" if s21 < VX_CONTANGO else "contango"
    out = {
        "found": True,
        "asof": asof.isoformat(),
        "vx": [{"e": e.isoformat(), "F": round(f, 2)} for e, f in mon[:3]],
        "vx2_vx1": s21,
        "vx3_vx1": s31,
        "state": state,
        "front_inverted": s31 is not None and s31 < 0,
        "carry_pct_month": round(100.0 * (v2 / v1 - 1.0), 1),
    }
    out["color"] = (
        "red" if (s21 < 0 or out["front_inverted"]) else "yellow" if s21 < VX_CONTANGO else "green"
    )
    return out


# ---------------------------------------------------------------- event gate
EVENT_KINDS = (
    ("CPI", ("cpi",)),
    ("Jobs", ("jobs", "payroll", "nfp", "employment")),
    ("FOMC", ("fomc",)),
    ("PCE", ("pce",)),
    ("GDP", ("gdp",)),
)


def event_kind(label: str, kind: str = "") -> str | None:
    lab = (label or "").lower()
    for name, keys in EVENT_KINDS:
        if any(k in lab for k in keys):
            return name
    return "FOMC" if (kind or "").lower() == "fed" else None


def move_ratio(closes: list[tuple[date, float]], event_dates: list[date]) -> dict:
    """Mean |close-to-close %| on event days vs on all days (same span)."""
    if len(closes) < 30:
        return {"n": 0}
    ch = {}
    for (d0, c0), (d1, c1) in zip(closes, closes[1:]):
        if c0:
            ch[d1] = abs(c1 / c0 - 1.0) * 100.0
    allm = sum(ch.values()) / len(ch)
    ev = [ch[d] for d in event_dates if d in ch]
    if not ev:
        return {"n": 0, "all_mean_pct": round(allm, 3)}
    em = sum(ev) / len(ev)
    return {
        "n": len(ev),
        "event_mean_pct": round(em, 3),
        "all_mean_pct": round(allm, 3),
        "ratio": round(em / allm, 2),
    }


def gate_color(ratio: float | None, move_pct: float | None) -> tuple[str, float]:
    hot_rates = move_pct is not None and move_pct >= 80
    if ratio is not None and ratio >= 1.5 and hot_rates:
        return "red", 0.5
    if (ratio is not None and ratio >= 1.5) or (ratio is not None and ratio >= 1.2 and hot_rates):
        return "yellow", 0.75
    if hot_rates and ratio is not None and ratio >= 1.0:
        return "yellow", 0.75
    return "green", 1.0


def event_gate(
    upcoming: list[dict], history: dict[str, dict], move_pct: float | None
) -> list[dict]:
    """upcoming = [{"d", "t", "kind", "label"}]; history = {kind_name: move_ratio(...)}."""
    out = []
    for ev in upcoming:
        k = event_kind(ev.get("label", ""), ev.get("kind", ""))
        if k is None:
            continue
        h = history.get(k) or {}
        col, size = gate_color(h.get("ratio"), move_pct)
        out.append(
            {
                "d": ev.get("d"),
                "t": ev.get("t", ""),
                "label": ev.get("label", ""),
                "kind": k,
                "n_past": h.get("n", 0),
                "event_mean_pct": h.get("event_mean_pct"),
                "ratio": h.get("ratio"),
                "move_pct_1y": move_pct,
                "color": col,
                "size": size,
            }
        )
    return out
