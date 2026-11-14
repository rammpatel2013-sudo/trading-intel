"""Daily 30-day 25-delta risk reversal per stock, rebuilt from CVForge daily option closes.

Created 2026-11-13 for the on-demand skew scan (build-queue #102). Same method as the skew study
(`research/skew_study_20261110/build.py`;
project doc `claude/skew-leading-indicator-study-20261110.md`):

* monthly expiries (third Friday), the one closest to 30 days out within 18-52 days;
* out-of-the-money puts and calls on a strike grid around the price, daily closes >= $0.05;
* Black-Scholes implied vol per contract (as-traded spot, piecewise short rate), then the IV at
  25-delta put / 25-delta call / 50-delta by linear interpolation in delta;
* RR = 25d put IV - 25d call IV.

Rows are cached per symbol in ``<cache_dir>/<SYM>.csv`` (one row per session tried; NaN when that
session could not be priced), so a name costs ~400-600 contract calls the first time and only the
new sessions after that. All HTTP goes through ``clients.cvforge`` (rule 1).
"""

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from trading_intel.errors import DataSourceError

if TYPE_CHECKING:
    from trading_intel.clients.cvforge import CVForgeClient

DTE_MIN, DTE_MAX, DTE_TARGET = 18, 52, 30
MIN_OPTION_PRICE = 0.05
CACHE_COLS = ["date", "spot", "dte", "ivp25", "ivc25", "atm"]
CACHE_VERSION = "g2"  # g2 = two-step strike grids, grid sized on the whole expiry window
# Third Fridays that were exchange holidays (expiry moves to Thursday).
_EXPIRY_HOLIDAYS = {date(2025, 4, 18), date(2026, 6, 19)}
_RATE_POINTS = [
    (2024.70, 0.053),
    (2024.95, 0.043),
    (2025.90, 0.039),
    (2026.90, 0.037),
    (2027.9, 0.037),
]


def short_rate(d: date) -> float:
    """Piecewise-linear USD short rate (same curve as the study; only the 2024+ part matters)."""
    y = d.year + (d.month - 1) / 12
    xs, ys = zip(*_RATE_POINTS)
    return float(np.interp(y, xs, ys))


def monthly_expiries(start: date, end: date) -> list[date]:
    """Third-Friday expiries from the month of ``start`` to ~3 months past ``end``."""
    out: list[date] = []
    y, m = start.year, start.month
    while True:
        d = date(y, m, 1)
        d += timedelta(days=(4 - d.weekday()) % 7 + 14)
        if d in _EXPIRY_HOLIDAYS:
            d -= timedelta(days=1)
        if d > end + timedelta(days=DTE_MAX + 31):
            return out
        out.append(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def strike_steps(price: float) -> tuple[float, ...]:
    """Listed strike spacings to try. Names list $1 or $2.50 (and $5 or $10) strikes at the same
    price, so both grids are pulled; the study's single-step grid missed e.g. TTD's $2.50 strikes.
    """
    if price < 25:
        return (0.5, 1.0, 2.5)
    if price < 60:
        return (1.0, 2.5)
    if price < 150:
        return (2.5, 5.0)
    return (5.0, 10.0)


def strike_grid(lo: float, hi: float, n: int, step0: float) -> list[float]:
    step = max(step0, math.ceil((hi - lo) / n / step0) * step0)
    a = math.floor(lo / step) * step
    return [round(a + i * step, 3) for i in range(int((hi - a) / step) + 2)]


def strike_grid_multi(lo: float, hi: float, n: int, steps: tuple[float, ...]) -> list[float]:
    return sorted({k for st in steps for k in strike_grid(lo, hi, n, st)})


def occ(sym: str, expiry: date, cp: str, k: float) -> str:
    return f"O:{sym}{expiry:%y%m%d}{cp}{int(round(k * 1000)):08d}"


def _ncdf(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, float)
    z = np.abs(x) / np.sqrt(2)
    t = 1 / (1 + 0.3275911 * z)
    y = 1 - (
        ((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592
    ) * t * np.exp(-z * z)
    return 0.5 * (1 + np.sign(x) * y)


def bs_price_delta(s: float, k: np.ndarray, t: float, r: float, v: np.ndarray, cp: str):
    sq = v * np.sqrt(t)
    d1 = (np.log(s / k) + (r + 0.5 * v * v) * t) / sq
    d2 = d1 - sq
    if cp == "C":
        return s * _ncdf(d1) - k * np.exp(-r * t) * _ncdf(d2), _ncdf(d1)
    return k * np.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1), _ncdf(d1) - 1


def implied_vols(s: float, k: np.ndarray, t: float, r: float, p: np.ndarray, cp: str) -> np.ndarray:
    """Vectorized bisection; NaN where the price is outside what any vol in 1.5%-390% gives."""
    lo = np.full_like(k, 0.01, dtype=float)
    hi = np.full_like(k, 4.0, dtype=float)
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        price, _ = bs_price_delta(s, k, t, r, mid, cp)
        hi = np.where(price > p, mid, hi)
        lo = np.where(price > p, lo, mid)
    v = 0.5 * (lo + hi)
    v[(v < 0.015) | (v > 3.9)] = np.nan
    return v


def _wing(s: float, t: float, r: float, quotes: list[tuple[float, float]], cp: str):
    """(25-delta IV, 50-delta IV or NaN) for one side, or None when it can't be read."""
    k = np.array([q[0] for q in quotes], float)
    p = np.array([q[1] for q in quotes], float)
    otm = (k <= s * 1.02) if cp == "P" else (k >= s * 0.98)
    k, p = k[otm], p[otm]
    if len(k) < 3:
        return None
    v = implied_vols(s, k, t, r, p, cp)
    ok = ~np.isnan(v)
    k, v = k[ok], v[ok]
    if len(k) < 3:
        return None
    _, de = bs_price_delta(s, k, t, r, v, cp)
    o = np.argsort(de)
    de, v = de[o], v[o]
    tgt, atm = (-0.25, -0.5) if cp == "P" else (0.25, 0.5)
    if not (de.min() - 0.08 <= tgt <= de.max() + 0.08):
        return None
    iv_atm = float(np.interp(atm, de, v)) if de.min() <= atm <= de.max() else float("nan")
    return float(np.interp(tgt, de, v)), iv_atm


def rows_from_bars(spot_by_day: dict[date, float], bars: list[tuple]) -> dict[date, tuple]:
    """Bars ``(expiry, 'P'|'C', strike, day, close)`` -> {day: (spot, dte, ivp25, ivc25, atm)}.

    Per day the expiry nearest 30 days (18-52) that gives both wings wins. Pure function (tested).
    """
    by: dict[tuple[date, date], dict[str, list]] = {}
    for e, cp, k, d, c in bars:
        if d in spot_by_day and c is not None and c >= MIN_OPTION_PRICE:
            by.setdefault((d, e), {}).setdefault(cp, []).append((k, c))
    out: dict[date, tuple] = {}
    for (d, e), g in by.items():
        dte = (e - d).days
        if not DTE_MIN <= dte <= DTE_MAX or "P" not in g or "C" not in g:
            continue
        s, t, r = spot_by_day[d], dte / 365, short_rate(d)
        put, call = _wing(s, t, r, g["P"], "P"), _wing(s, t, r, g["C"], "C")
        if put is None or call is None:
            continue
        atms = [x for x in (put[1], call[1]) if not math.isnan(x)]
        atm = float(np.mean(atms)) if atms else float("nan")
        prev = out.get(d)
        if prev is None or abs(dte - DTE_TARGET) < abs(prev[1] - DTE_TARGET):
            out[d] = (s, dte, put[0], call[0], atm)
    return out


class _RateGate:
    """At most ``per_min`` calls in any 60 s, shared by the worker threads."""

    def __init__(self, per_min: int) -> None:
        self.per_min, self._stamps, self._lock = per_min, [], threading.Lock()

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                self._stamps = [x for x in self._stamps if now - x < 60]
                if len(self._stamps) < self.per_min:
                    self._stamps.append(now)
                    return
            time.sleep(0.05)


class RRHistory:
    """Cached daily RR series per symbol, filled from CVForge on demand."""

    def __init__(
        self, client: CVForgeClient, cache_dir: Path, *, workers: int = 8, per_min: int = 600
    ) -> None:
        self.client, self.cache_dir, self.workers = client, Path(cache_dir), workers
        self.gate = _RateGate(per_min)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, sym: str) -> Path:
        return self.cache_dir / f"{sym.upper()}_{CACHE_VERSION}.csv"

    def load(self, sym: str) -> pd.DataFrame:
        p = self._path(sym)
        if not p.exists():
            return pd.DataFrame(columns=CACHE_COLS)
        df = pd.read_csv(p, parse_dates=["date"])
        df["date"] = df["date"].dt.date
        return df

    def closes(
        self, sym: str, frm: date, to: date, *, adjusted: bool | None = None
    ) -> dict[date, float]:
        """Daily stock closes by session date (``adjusted=False`` = as traded that day).

        FMP end-of-day prices via the CVForge passthrough first (fast, has today's close after the
        bell); CVForge /mas daily bars as the fallback (they 502 often and lag a session).
        """
        ep, field = (
            ("historical-price-eod/non-split-adjusted", "adjClose")
            if adjusted is False
            else ("historical-price-eod/full", "close")
        )
        rows: Any = []
        for attempt in range(3):
            self.gate.wait()
            try:
                rows = self.client.fmp(
                    ep, {"symbol": sym.upper(), "from": f"{frm:%Y-%m-%d}", "to": f"{to:%Y-%m-%d}"}
                )
                break
            except DataSourceError as exc:
                if any(c in str(exc) for c in ("-> 400", "-> 404")):
                    break
                time.sleep(1 + attempt)
        if isinstance(rows, dict):
            rows = rows.get("historical", [])
        out = {
            date.fromisoformat(str(r["date"])[:10]): float(r[field])
            for r in (rows or [])
            if isinstance(r, dict) and r.get(field) is not None
        }
        return out or dict(self._bars(sym.upper(), frm, to, adjusted=adjusted))

    def _bars(
        self, ticker: str, frm: date, to: date, *, adjusted: bool | None = None
    ) -> list[tuple[date, float]]:
        for attempt in range(3):
            self.gate.wait()
            try:
                df = self.client.aggs(
                    ticker, frm=f"{frm:%Y-%m-%d}", to=f"{to:%Y-%m-%d}", adjusted=adjusted
                )
                break
            except DataSourceError as exc:
                if any(c in str(exc) for c in ("-> 400", "-> 404")):
                    return []
                time.sleep(1 + attempt)
        else:
            return []
        if df.empty or "c" not in df.columns:
            return []
        # Daily bars are stamped at 00:00 New York (05:00 UTC) -> shift before taking the date.
        days = (df["ts"] + pd.Timedelta(hours=6)).dt.date
        return list(zip(days, df["c"]))

    def update(
        self, sym: str, spot_by_day: dict[date, float], start: date, *, budget_s: float = 150.0
    ) -> pd.DataFrame:
        """Fill each session of ``spot_by_day`` (as-traded closes) from ``start`` not yet cached.

        Raises ``TimeoutError`` (and caches nothing) when the vendor is too slow to finish inside
        ``budget_s`` - a half-pulled strike grid would cache wrong readings.
        """
        sym = sym.upper()
        have = self.load(sym)
        tried = set(have["date"]) if len(have) else set()
        todo = sorted(d for d in spot_by_day if d >= start and d not in tried)
        if not todo:
            return have
        tasks = []
        for e in monthly_expiries(todo[0], todo[-1]):
            win = [d for d in todo if DTE_MIN <= (e - d).days <= DTE_MAX]
            if not win:
                continue
            # Size the grid on every session of this expiry's window (not just the new ones), so a
            # one-day top-up uses the same strikes a full pull would.
            full = [d for d in spot_by_day if d >= start and DTE_MIN <= (e - d).days <= DTE_MAX]
            smin = min(spot_by_day[d] for d in full)
            smax = max(spot_by_day[d] for d in full)
            steps = strike_steps(smax)
            for k in strike_grid_multi(0.70 * smin, 1.02 * smax, 9, steps):
                tasks.append((e, "P", k, win[0], win[-1]))
            for k in strike_grid_multi(0.98 * smin, 1.30 * smax, 9, steps):
                tasks.append((e, "C", k, win[0], win[-1]))

        def fetch(task: tuple) -> list[tuple]:
            e, cp, k, frm, to = task
            return [(e, cp, k, d, c) for d, c in self._bars(occ(sym, e, cp, k), frm, to)]

        bars: list[tuple] = []
        ex = ThreadPoolExecutor(self.workers)
        futs = [ex.submit(fetch, t) for t in tasks]
        try:
            for f in as_completed(futs, timeout=budget_s):
                bars.extend(f.result())
        except FuturesTimeout as exc:
            raise TimeoutError(f"{sym}: option history not finished in {budget_s:.0f} s") from exc
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        rows = rows_from_bars({d: spot_by_day[d] for d in todo}, bars)
        new = pd.DataFrame(
            [
                (d, *rows[d]) if d in rows else (d, spot_by_day[d], np.nan, np.nan, np.nan, np.nan)
                for d in todo
            ],
            columns=CACHE_COLS,
        )
        out = pd.concat([have, new], ignore_index=True) if len(have) else new
        out = out.drop_duplicates("date", keep="last").sort_values("date")
        # A session from the last few days that could not be priced may just not be published yet:
        # keep it out of the cache so the next call tries it again.
        fresh = max(spot_by_day) - timedelta(days=4)
        keep = ~(out["ivp25"].isna() & (out["date"] > fresh))
        out[keep].to_csv(self._path(sym), index=False, float_format="%.6f")
        return out
