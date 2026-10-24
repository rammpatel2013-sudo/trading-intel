"""Far-OTM put-IV floor tracker — the "protection is cheap → protection gets bid
while the index sits at highs → correction follows" pattern (SPY vs Avg IV of
F-OTM puts, the @exporiskprivate chart).

Pure transforms only (rows in → states / clusters / forward stats out); no DB
or vendor dependency, so everything here is unit-testable. Descriptor only
(FlashAlpha rule 4): it marks STATES and tabulates what SPY did after prior
occurrences — it is not a signal.

Series used: SPY 25Δ-put IV at constant-maturity 30d from ``iv_tenor_snapshots``.
At VIX ~15 a 2–3 % OTM (15–25 pt) SPY put is ~25–30Δ, so the delta series is the
same object as the chart's fixed-price-distance series, up to a small lag in
spikes (fixed-$ strikes gain delta as vol rises).

History before our collector (2026-06-23) comes from the vendor chart itself,
digitized once into ``data/reference/fotm_put_iv_chart_history.csv`` (decided
2026-10-24: image data is used ONLY by this report, never written to the DB).
``splice`` keeps chart rows strictly before the first DB date; DB rows win.

States (decided 2026-10-22):
  FLOOR  = put IV <= ``floor_iv`` (fixed absolute level, default 13.0 %)
  WARN   = put IV >= ``reclaim_iv`` (17 %) AND SPY within ``near_high_pct`` (2 %)
           of its trailing ``high_lookback`` (60-session) high
  NEUTRAL otherwise
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

FLOOR_IV = 0.13
RECLAIM_IV = 0.17
NEAR_HIGH_PCT = 0.02
HIGH_LOOKBACK = 60
MIN_EXPIRIES = 10  # rows with fewer listed expiries are bad pulls → dropped
FWD_HORIZONS = (16, 20)
DD_HORIZON = 30


def _fin(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


@dataclass
class Point:
    ts: date
    iv: float
    spot: float
    high60: float | None = None
    state: str = "NEUTRAL"
    source: str = "db"


@dataclass
class Cluster:
    """One floor episode = consecutive FLOOR days (gaps of <= ``merge_gap`` sessions merged)."""

    start: date
    end: date
    n_touches: int
    min_iv: float
    spot_at_floor: float  # spot on the last floor day
    warn_date: date | None = None
    days_to_warn: int | None = None
    spot_at_warn: float | None = None
    fwd: dict = field(default_factory=dict)  # {"floor_16d": r, "floor_20d": r, "warn_16d": ...}
    max_dd_30d: float | None = None  # from last floor day, worst close over next 30 sessions
    source: str = "db"  # "chart" when the floor days come from the digitized vendor chart


def clean_rows(rows: list[dict], *, min_expiries: int = MIN_EXPIRIES) -> list[Point]:
    """Rows (ts, iv_put_25d, spot, n_expiries) → sorted Points; drops bad pulls."""
    out: list[Point] = []
    for r in rows:
        iv = _fin(r.get("iv_put_25d"))
        spot = _fin(r.get("spot"))
        n_exp = r.get("n_expiries")
        ts = r.get("ts")
        if iv is None or spot is None or ts is None:
            continue
        if n_exp is not None and int(n_exp) < min_expiries:
            continue
        if not (0.0 < iv < 2.0):
            continue
        if hasattr(ts, "date") and not isinstance(ts, date):
            ts = ts.date()
        out.append(Point(ts=ts, iv=iv, spot=spot, source=str(r.get("source") or "db")))
    out.sort(key=lambda p: p.ts)
    # de-dup same-day rows (keep last)
    dedup: dict[date, Point] = {}
    for p in out:
        dedup[p.ts] = p
    return [dedup[k] for k in sorted(dedup)]


def splice(chart_rows: list[dict], db_rows: list[dict]) -> list[dict]:
    """Chart-history rows strictly before the first DB date, then all DB rows.

    Chart rows are tagged ``source="chart"``; DB rows ``source="db"``. If there are
    no DB rows the chart rows are returned alone.
    """
    def _d(r):
        ts = r.get("ts")
        return ts.date() if hasattr(ts, "date") and not isinstance(ts, date) else ts

    db = [dict(r, source="db") for r in db_rows if r.get("ts") is not None]
    first = min((_d(r) for r in db), default=None)
    ch = [dict(r, source="chart") for r in chart_rows
          if r.get("ts") is not None and (first is None or _d(r) < first)]
    return ch + db


def label_states(
    pts: list[Point],
    *,
    floor_iv: float = FLOOR_IV,
    reclaim_iv: float = RECLAIM_IV,
    near_high_pct: float = NEAR_HIGH_PCT,
    high_lookback: int = HIGH_LOOKBACK,
) -> list[Point]:
    """Attach trailing high + state to every point (in place; returns the list).

    WARN needs a FULL ``high_lookback`` window: with fewer sessions the "trailing
    high" is just the max of a few days, so "near the high" is trivially true
    (that produced false WARN dots at the start of the history).
    """
    for i, p in enumerate(pts):
        window = pts[max(0, i - high_lookback + 1) : i + 1]
        p.high60 = max(q.spot for q in window)
        near_high = len(window) >= high_lookback and p.spot >= p.high60 * (1.0 - near_high_pct)
        if p.iv <= floor_iv:
            p.state = "FLOOR"
        elif p.iv >= reclaim_iv and near_high:
            p.state = "WARN"
        else:
            p.state = "NEUTRAL"
    return pts


def _fwd_ret(pts: list[Point], i: int, n: int) -> float | None:
    if i + n >= len(pts) or pts[i].spot == 0:
        return None
    return pts[i + n].spot / pts[i].spot - 1.0


def _max_dd(pts: list[Point], i: int, n: int) -> float | None:
    if i + 1 >= len(pts) or pts[i].spot == 0:
        return None
    seg = pts[i + 1 : i + 1 + n]
    if not seg:
        return None
    return min(q.spot for q in seg) / pts[i].spot - 1.0


def find_clusters(
    pts: list[Point],
    *,
    merge_gap: int = 3,
    horizons: tuple[int, ...] = FWD_HORIZONS,
    dd_horizon: int = DD_HORIZON,
) -> list[Cluster]:
    """Group FLOOR days into episodes and measure what came after each."""
    idx = [i for i, p in enumerate(pts) if p.state == "FLOOR"]
    if not idx:
        return []
    groups: list[list[int]] = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= merge_gap:
            groups[-1].append(i)
        else:
            groups.append([i])

    out: list[Cluster] = []
    for g in groups:
        first, last = g[0], g[-1]
        c = Cluster(
            start=pts[first].ts,
            end=pts[last].ts,
            n_touches=len(g),
            min_iv=min(pts[i].iv for i in g),
            spot_at_floor=pts[last].spot,
            source="chart" if any(pts[i].source == "chart" for i in g) else "db",
        )
        # first WARN after the last floor day (before the next cluster starts)
        nxt_start = None
        gi = groups.index(g)
        if gi + 1 < len(groups):
            nxt_start = groups[gi + 1][0]
        for j in range(last + 1, len(pts)):
            if nxt_start is not None and j >= nxt_start:
                break
            if pts[j].state == "WARN":
                c.warn_date = pts[j].ts
                c.days_to_warn = j - last
                c.spot_at_warn = pts[j].spot
                for n in horizons:
                    c.fwd[f"warn_{n}d"] = _fwd_ret(pts, j, n)
                break
        for n in horizons:
            c.fwd[f"floor_{n}d"] = _fwd_ret(pts, last, n)
        c.max_dd_30d = _max_dd(pts, last, dd_horizon)
        out.append(c)
    return out


def current_read(pts: list[Point], clusters: list[Cluster], *, reclaim_iv: float = RECLAIM_IV) -> dict:
    """Header descriptor: state now, days since last floor, IV vs lines."""
    if not pts:
        return {"state": "NO DATA"}
    last = pts[-1]
    last_floor = None
    for p in reversed(pts):
        if p.state == "FLOOR":
            last_floor = p
            break
    days_since = None
    if last_floor is not None:
        days_since = sum(1 for p in pts if p.ts > last_floor.ts)
    # days since the current WARN episode began (consecutive-ish WARN run)
    warn_run = 0
    for p in reversed(pts):
        if p.state == "WARN":
            warn_run += 1
        else:
            break
    floor_min = min((p.iv for p in pts), default=None)
    text = {
        "FLOOR": "protection at the floor — max complacency; the two prior episodes drifted higher for ~3 weeks before correcting",
        "WARN": "protection being bid while SPY sits at highs — the chart's warning state; prior episodes topped 16–20 sessions after the floor",
        "NEUTRAL": "no state — either IV is off the floor without SPY at highs, or SPY has already pulled back",
    }[last.state]
    return {
        "as_of": last.ts.isoformat(),
        "state": last.state,
        "iv": last.iv,
        "spot": last.spot,
        "high60": last.high60,
        "pct_off_high": (last.spot / last.high60 - 1.0) if last.high60 else None,
        "reclaim_iv": reclaim_iv,
        "iv_vs_reclaim": last.iv - reclaim_iv,
        "last_floor_date": last_floor.ts.isoformat() if last_floor else None,
        "last_floor_iv": last_floor.iv if last_floor else None,
        "days_since_floor": days_since,
        "warn_run_days": warn_run,
        "series_min_iv": floor_min,
        "n_clusters": len(clusters),
        "first_date": pts[0].ts.isoformat(),
        "db_start": next((p.ts.isoformat() for p in pts if p.source == "db"), None),
        "read": text,
    }


def build_view(rows: list[dict], **kw) -> dict:
    """rows → {points, clusters, read}. ``kw`` forwards state thresholds."""
    pts = label_states(clean_rows(rows), **{k: v for k, v in kw.items() if k in (
        "floor_iv", "reclaim_iv", "near_high_pct", "high_lookback")})
    clusters = find_clusters(pts)
    return {
        "points": pts,
        "clusters": clusters,
        "read": current_read(pts, clusters, reclaim_iv=kw.get("reclaim_iv", RECLAIM_IV)),
        "params": {
            "floor_iv": kw.get("floor_iv", FLOOR_IV),
            "reclaim_iv": kw.get("reclaim_iv", RECLAIM_IV),
            "near_high_pct": kw.get("near_high_pct", NEAR_HIGH_PCT),
            "high_lookback": kw.get("high_lookback", HIGH_LOOKBACK),
        },
    }
