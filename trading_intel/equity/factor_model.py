"""Factor risk model - the toraniko method (github.com/0xfdf/toraniko), numpy only.

Each day t, across that day's universe:

    r_i(t) = f_market + f_sector(i) + sum_k z_ik(t-1) * f_k + e_i(t)

* weights   = sqrt(market cap) (big names count more, but not linearly)
* sectors   = one dummy per sector, constrained so the cap-weighted sector returns sum to 0
              (then f_market is the cap-weighted market move, sectors are moves vs the market)
* styles    = the z-scores from ``styles`` as of the previous close (no look-ahead)
* returns are clipped at the 1st / 99th percentile for the fit; the residual e_i(t) is taken on the
  RAW return, so a stock's own news move stays in its own move.

``e_i(t)`` is the stock's OWN move that day - what is left after the market, its sector and the
style tides are taken out. Summed over months it shows which stocks are strong for their own
reasons (the candidate list ranks on this).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from trading_intel.equity.panel import Panel
from trading_intel.equity.styles import STYLES, compute_styles


@dataclass
class DayFit:
    factors: dict[str, float]
    kinds: dict[str, str]
    resid: np.ndarray  # length N, NaN outside the day's universe
    n: int
    r2: float | None


@dataclass
class ModelRun:
    dates: list
    symbols: list[str]
    fits: list[DayFit | None]
    styles: dict[str, np.ndarray] = field(default_factory=dict)
    resid: np.ndarray | None = None  # T x N own moves
    returns: np.ndarray | None = None


def fit_day(
    r: np.ndarray,
    mcap: np.ndarray,
    sectors: Sequence[str | None],
    z: np.ndarray,
    ok: np.ndarray,
    *,
    style_names: Sequence[str] = STYLES,
    min_n: int = 60,
) -> DayFit | None:
    """One cross-sectional constrained WLS. ``z`` = N x K style exposures."""
    ok = ok & np.isfinite(r) & np.isfinite(mcap) & (mcap > 0)
    if ok.sum() < min_n:
        return None
    idx = np.flatnonzero(ok)
    rr = r[idx]
    lo, hi = np.percentile(rr, [1, 99])
    rfit = np.clip(rr, lo, hi)
    cap = mcap[idx]
    w = np.sqrt(cap)
    secs = [sectors[i] or "Unknown" for i in idx]
    names = sorted(set(secs))
    k = len(names)
    S = np.zeros((len(idx), k))
    for j, s in enumerate(secs):
        S[j, names.index(s)] = 1.0
    c = np.array([cap[S[:, j] > 0].sum() for j in range(k)])
    c = c / c.sum()
    Z = np.nan_to_num(z[idx], nan=0.0)
    if k > 1:
        S_eff = S[:, :-1] - S[:, -1:] * (c[:-1] / c[-1])[None, :]
    else:
        S_eff = np.zeros((len(idx), 0))
    X = np.hstack([np.ones((len(idx), 1)), S_eff, Z])
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], rfit * sw, rcond=None)
    f_mkt = beta[0]
    f_sec = np.zeros(k)
    if k > 1:
        f_sec[:-1] = beta[1:k]
        f_sec[-1] = -np.dot(c[:-1], f_sec[:-1]) / c[-1]
    f_sty = beta[k:] if k > 1 else beta[1:]
    fitted = f_mkt + S @ f_sec + Z @ f_sty
    resid = np.full(r.shape, np.nan)
    resid[idx] = rr - fitted
    e_fit = rfit - fitted
    ybar = np.average(rfit, weights=w)
    ss_tot = np.sum(w * (rfit - ybar) ** 2)
    r2 = float(1 - np.sum(w * e_fit**2) / ss_tot) if ss_tot > 0 else None
    factors: dict[str, float] = {"market": float(f_mkt)}
    kinds: dict[str, str] = {"market": "market"}
    for j, nm in enumerate(names):
        factors[f"sector:{nm}"] = float(f_sec[j])
        kinds[f"sector:{nm}"] = "sector"
    for j, nm in enumerate(style_names):
        factors[nm] = float(f_sty[j])
        kinds[nm] = "style"
    return DayFit(factors, kinds, resid, len(idx), r2)


def run_model(
    p: Panel, *, styles: Mapping[str, np.ndarray] | None = None, start_row: int = 0
) -> ModelRun:
    """Fit every day from ``start_row`` (exposures from the previous row)."""
    sty = dict(styles) if styles is not None else compute_styles(p)
    r = p.returns
    T, N = r.shape
    Zall = np.stack([sty[s] for s in STYLES], axis=2)  # T x N x K
    fits: list[DayFit | None] = [None] * T
    resid = np.full((T, N), np.nan)
    for t in range(max(1, start_row), T):
        prev_ok = p.member[t - 1] & ~np.isnan(Zall[t - 1]).all(axis=1)
        fit = fit_day(r[t], p.mcap[t - 1], p.sector, Zall[t - 1], prev_ok & p.member[t])
        fits[t] = fit
        if fit is not None:
            resid[t] = fit.resid
    return ModelRun(p.dates, p.symbols, fits, sty, resid, r)


def factor_rows(run: ModelRun, *, last: int | None = None) -> list[dict]:
    rows = []
    rng = (
        range(len(run.dates))
        if last is None
        else range(max(0, len(run.dates) - last), len(run.dates))
    )
    for t in rng:
        fit = run.fits[t]
        if fit is None:
            continue
        for name, val in fit.factors.items():
            rows.append(
                {
                    "date": run.dates[t],
                    "factor": name,
                    "ret": val,
                    "kind": fit.kinds[name],
                    "n_stocks": fit.n,
                    "r2": fit.r2,
                }
            )
    return rows


def stock_rows(run: ModelRun, sectors: Sequence[str | None], *, last: int = 5) -> list[dict]:
    rows = []
    T = len(run.dates)
    for t in range(max(0, T - last), T):
        if run.fits[t] is None or run.resid is None or run.returns is None:
            continue
        for i, sym in enumerate(run.symbols):
            e = run.resid[t, i]
            if np.isnan(e):
                continue
            row = {
                "date": run.dates[t],
                "symbol": sym,
                "sector": sectors[i],
                "ret": float(run.returns[t, i]),
                "resid": float(e),
            }
            for s in STYLES:
                v = run.styles[s][t, i]
                row[s] = None if np.isnan(v) else float(v)
            rows.append(row)
    return rows


def own_move(run: ModelRun, n: int, *, skip: int = 0) -> np.ndarray:
    """Sum of the last ``n`` own moves per stock (ending ``skip`` days ago); NaN if < 70% covered."""
    R = run.resid
    if R is None or R.shape[0] == 0:
        return np.array([])
    end = R.shape[0] - skip
    win = R[max(0, end - n) : end]
    cnt = (~np.isnan(win)).sum(axis=0)
    out = np.nansum(win, axis=0)
    out[cnt < 0.7 * n] = np.nan
    return out


def covariance(
    rows: Sequence[Mapping], factors: Sequence[str], *, halflife: int = 90
) -> np.ndarray:
    """Exponentially weighted factor covariance from stored factor returns (date-sorted rows)."""
    by_date: dict = {}
    for r in rows:
        by_date.setdefault(r["date"], {})[r["factor"]] = r["ret"]
    dates = sorted(by_date)
    F = np.array(
        [
            [by_date[d].get(f) if by_date[d].get(f) is not None else 0.0 for f in factors]
            for d in dates
        ],
        dtype=float,
    )
    if len(F) < 2:
        return np.zeros((len(factors), len(factors)))
    w = 0.5 ** (np.arange(len(F))[::-1] / halflife)
    w = w / w.sum()
    mu = (w[:, None] * F).sum(axis=0)
    D = F - mu
    return (w[:, None] * D).T @ D
