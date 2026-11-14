"""Style exposures (date x symbol) for the factor risk model. Pure numpy.

Seven styles, each standardized per day across that day's universe (Barra convention: winsorized
1-99%, cap-weighted mean 0, equal-weighted st.dev 1, missing = 0 = neutral):

* momentum   - 12-1 month log return (t-252 .. t-21)
* size       - log market cap
* value      - mean of book/price, sales/price, cash-flow/price (toraniko's three)
* beta       - 252-day beta to the cap-weighted universe return
* volatility - 63-day daily st.dev, with beta taken out (so it is not beta twice)
* quality    - mean of ROE, gross profit / assets, minus debt / assets
* growth     - trailing-12-month revenue growth vs a year earlier

Row t = what was known at the close of day t; the model pairs row t-1 with day t's return.
"""

from __future__ import annotations

import warnings

import numpy as np

from trading_intel.equity.panel import Panel

STYLES = ("momentum", "size", "value", "beta", "volatility", "quality", "growth")


def _rolling_sum(x: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """NaN-aware trailing n-row sum and count along axis 0 (window ends at row t, inclusive)."""
    valid = ~np.isnan(x)
    v = np.where(valid, x, 0.0)
    cs = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(v, axis=0)])
    cc = np.vstack([np.zeros((1, x.shape[1])), np.cumsum(valid, axis=0)])
    s = cs[n:] - cs[:-n] if x.shape[0] >= n else np.zeros((0, x.shape[1]))
    c = cc[n:] - cc[:-n] if x.shape[0] >= n else np.zeros((0, x.shape[1]))
    pad = np.full((min(n - 1, x.shape[0]), x.shape[1]), np.nan)
    return np.vstack([pad, s]), np.vstack([pad, c])


def market_return(p: Panel, r: np.ndarray | None = None) -> np.ndarray:
    """Cap-weighted universe return per day (weights = previous day's market cap)."""
    r = p.returns if r is None else r
    w = np.vstack([np.full((1, r.shape[1]), np.nan), p.mcap[:-1]])
    ok = p.member & ~np.isnan(r) & ~np.isnan(w) & (w > 0)
    num = np.where(ok, r * w, 0.0).sum(axis=1)
    den = np.where(ok, w, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def momentum_raw(
    r: np.ndarray, *, long: int = 252, skip: int = 21, min_obs: int = 200
) -> np.ndarray:
    lr = np.log1p(np.where(r > -1, r, np.nan))
    s_long, c_long = _rolling_sum(lr, long)
    s_skip, c_skip = _rolling_sum(lr, skip)
    out = s_long - s_skip
    out[(c_long - c_skip) < (min_obs - skip)] = np.nan
    return out


def beta_raw(r: np.ndarray, m: np.ndarray, *, n: int = 252, min_obs: int = 150) -> np.ndarray:
    mm = np.broadcast_to(m[:, None], r.shape).copy()
    both = ~np.isnan(r) & ~np.isnan(mm)
    rr = np.where(both, r, np.nan)
    mm = np.where(both, mm, np.nan)
    s_r, c = _rolling_sum(rr, n)
    s_m, _ = _rolling_sum(mm, n)
    s_rm, _ = _rolling_sum(rr * mm, n)
    s_mm, _ = _rolling_sum(mm * mm, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = s_rm / c - (s_r / c) * (s_m / c)
        var = s_mm / c - (s_m / c) ** 2
        out = cov / var
    out[c < min_obs] = np.nan
    return out


def vol_raw(r: np.ndarray, *, n: int = 63, min_obs: int = 40) -> np.ndarray:
    s, c = _rolling_sum(r, n)
    s2, _ = _rolling_sum(r * r, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        var = s2 / c - (s / c) ** 2
        out = np.sqrt(np.clip(var, 0, None))
    out[c < min_obs] = np.nan
    return out


def standardize(
    raw: np.ndarray, w: np.ndarray, member: np.ndarray, *, fill: float = 0.0
) -> np.ndarray:
    """Per row: winsorize 1-99%, cap-weighted mean 0, st.dev 1 over members; non-members NaN."""
    out = np.full(raw.shape, np.nan)
    for t in range(raw.shape[0]):
        m = member[t]
        if not m.any():
            continue
        x = raw[t, m].astype(float)
        ok = ~np.isnan(x)
        if ok.sum() < 20:
            out[t, m] = fill
            continue
        lo, hi = np.nanpercentile(x[ok], [1, 99])
        x = np.clip(x, lo, hi)
        ww = w[t, m]
        wok = ok & ~np.isnan(ww) & (ww > 0)
        mu = np.average(x[wok], weights=ww[wok]) if wok.any() else np.nanmean(x[ok])
        sd = np.nanstd(x[ok])
        z = (x - mu) / sd if sd > 0 else np.zeros_like(x)
        z[~ok] = fill
        out[t, m] = z
    return out


def _mean_z(parts: list[np.ndarray]) -> np.ndarray:
    st = np.stack(parts)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN cells (non-members) stay NaN
        return np.nanmean(st, axis=0)


def _ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        out = a / b
    out[~np.isfinite(out) | (b <= 0)] = np.nan
    return out


def _orthogonalize(y: np.ndarray, x: np.ndarray, member: np.ndarray) -> np.ndarray:
    """Per row: residual of y on x (members, both finite)."""
    out = y.copy()
    for t in range(y.shape[0]):
        m = member[t] & ~np.isnan(y[t]) & ~np.isnan(x[t])
        if m.sum() < 20:
            continue
        xx, yy = x[t, m], y[t, m]
        vx = np.var(xx)
        b = np.cov(xx, yy, bias=True)[0, 1] / vx if vx > 0 else 0.0
        out[t, m] = yy - b * xx
    return out


def compute_styles(p: Panel) -> dict[str, np.ndarray]:
    """{style: z-score panel}. Row t uses data up to and including day t."""
    r = p.returns
    m = market_return(p, r)
    w = p.mcap
    mem = p.member
    f = p.fund
    std = lambda raw: standardize(raw, w, mem)  # noqa: E731
    out: dict[str, np.ndarray] = {}
    out["momentum"] = std(momentum_raw(r))
    with np.errstate(divide="ignore", invalid="ignore"):
        out["size"] = std(np.log(np.where(w > 0, w, np.nan)))
    beta_z = std(beta_raw(r, m))
    out["beta"] = beta_z
    vol_z = std(vol_raw(r))
    out["volatility"] = std(_orthogonalize(vol_z, beta_z, mem))
    if f:
        out["value"] = std(
            _mean_z(
                [
                    std(_ratio(f["book_equity"], w)),
                    std(_ratio(f["ttm_revenue"], w)),
                    std(_ratio(f["ttm_ocf"], w)),
                ]
            )
        )
        debt = _ratio(f["total_debt"], f["total_assets"])
        out["quality"] = std(
            _mean_z(
                [
                    std(_ratio(f["ttm_net_income"], f["book_equity"])),
                    std(_ratio(f["ttm_gross_profit"], f["total_assets"])),
                    -std(debt),
                ]
            )
        )
        out["growth"] = std(f["rev_growth"])
    else:
        z = np.where(mem, 0.0, np.nan)
        out["value"] = z.copy()
        out["quality"] = z.copy()
        out["growth"] = z.copy()
    return {s: out[s] for s in STYLES}
