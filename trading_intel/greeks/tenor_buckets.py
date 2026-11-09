"""Dealer gamma and vega split by TENOR bucket (created 2026-11-09).

BofA "Exhibit 7" style view: the naive dealer book's $gamma per 1% move recomputed
on a spot ladder, one curve per tenor bucket plus the total, and the dealer $vega
per vol point by the same buckets.

Buckets are by BUSINESS days (sessions) from the book day to the expiry, counted
with the repo's own NYSE calendar (``timeutils.is_trading_session``):

    1d      1 session          (the next expiry)
    2d_1w   2-5 sessions
    1w_1m   6-21 sessions
    gt_1m   more than 21 sessions

Conventions (same as ``greeks.exposure_profile`` / ``get_profile`` so the numbers
tie out): Black-Scholes, r = q = 0, calendar time ``(expiry - book_day) / 365``,
sticky strike (each strike keeps its stored IV as spot moves), multiplier 100.

    gamma$ = sign * gamma * oi * 100 * S^2 * 0.01     -> $bn per 1% move
    vega$  = sign * vega  * oi * 100 * 0.01           -> $mn per 1 vol point

Naive sign: calls +1, puts -1 (dealers long calls, short puts - which is the same
book as "customers long puts, short calls"). Vega is also reported under two
other conventions so the reader sees how much the sign assumption carries:
``short_all`` (customers long every option: dealers short calls AND puts) and
``flipped`` (customers long calls, short puts: the naive book times -1).

Descriptor only (CLAUDE.md rule 4): positioning read, not a trade signal.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from trading_intel.greeks.black_scholes import bs_gamma, bs_vega, years_to_expiry
from trading_intel.timeutils import is_trading_session

BUCKETS: tuple[tuple[str, str], ...] = (
    ("1d", "1 day"),
    ("2d_1w", "2 days - 1 week"),
    ("1w_1m", "1 week - 1 month"),
    ("gt_1m", "> 1 month"),
)
BUCKET_KEYS = tuple(k for k, _ in BUCKETS)
DISCLAIMER = "positioning read, not a trade signal"
GAMMA_UNITS = "$bn of SPX delta per 1% move (naive dealer book)"
VEGA_UNITS = "$mn per 1 vol point (naive dealer book)"
_NEEDED = {"strike", "cp", "iv", "oi", "expiry"}


def sessions_to_expiry(book_day: date, expiry: date) -> int:
    """Trading sessions after ``book_day`` up to and including ``expiry`` (0 = same day)."""
    if expiry <= book_day:
        return 0
    n, d = 0, book_day
    while d < expiry:
        d += timedelta(days=1)
        if is_trading_session(d):
            n += 1
    # An expiry dated on a holiday/weekend still counts as at least one session away.
    return max(n, 1)


def bucket_for(sessions: int) -> str:
    """Tenor bucket key for a session count (0 and 1 both read as the 1-day bucket)."""
    if sessions <= 1:
        return "1d"
    if sessions <= 5:
        return "2d_1w"
    if sessions <= 21:
        return "1w_1m"
    return "gt_1m"


def spot_ladder(spot: float, *, span: float = 0.12, step: float = 25.0) -> np.ndarray:
    """Round-step ladder covering spot +/- ``span`` (multiples of ``step``)."""
    lo = np.floor(spot * (1 - span) / step) * step
    hi = np.ceil(spot * (1 + span) / step) * step
    return np.arange(lo, hi + step / 2, step, dtype=float)


def zero_cross(grid: np.ndarray, values: np.ndarray, near: float) -> float | None:
    """Zero crossing of ``values`` over ``grid`` nearest ``near`` (linear interpolation).

    Returns None when the curve never changes sign on the ladder. A grid point that is
    exactly zero counts as a crossing at that point.
    """
    g = np.asarray(grid, dtype=float)
    v = np.asarray(values, dtype=float)
    out: list[float] = []
    for i in range(1, len(g)):
        a, b = v[i - 1], v[i]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        if a == 0.0:
            out.append(float(g[i - 1]))
        elif a * b < 0:
            out.append(float(g[i - 1] + (g[i] - g[i - 1]) * (0.0 - a) / (b - a)))
    if len(v) and v[-1] == 0.0:
        out.append(float(g[-1]))
    if not out:
        return None
    return min(out, key=lambda x: abs(x - near))


def prepare_chain(chain: pd.DataFrame, book_day: date) -> pd.DataFrame:
    """Clean a raw book: live expiries only, iv > 0, oi > 0; adds sign, sessions, bucket."""
    if chain is None or chain.empty or not _NEEDED.issubset(chain.columns):
        return pd.DataFrame(columns=[*_NEEDED, "sign", "sessions", "bucket"])
    df = chain.dropna(subset=list(_NEEDED)).copy()
    df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
    df = df[(df["iv"] > 0) & (df["oi"] > 0) & (df["expiry"] > book_day)]
    if df.empty:
        return df.assign(sign=[], sessions=[], bucket=[])
    df["sign"] = np.where(df["cp"].astype(str).str.upper().str[0] == "C", 1.0, -1.0)
    sess = {e: sessions_to_expiry(book_day, e) for e in df["expiry"].unique()}
    df["sessions"] = df["expiry"].map(sess).astype(int)
    df["bucket"] = df["sessions"].map(bucket_for)
    return df.reset_index(drop=True)


def _years(df: pd.DataFrame, book_day: date) -> np.ndarray:
    return years_to_expiry(pd.to_datetime(df["expiry"]), book_day)


def _share(part: float, total: float) -> float | None:
    return None if not total else round(100.0 * part / total, 1)


def tenor_gamma(
    chain: pd.DataFrame, spot: float, book_day: date, *, span: float = 0.12, step: float = 25.0
) -> dict:
    """Gamma-by-tenor curves on the spot ladder + value/zero-cross/share per bucket."""
    df = prepare_chain(chain, book_day)
    if df.empty or not np.isfinite(spot) or spot <= 0:
        return {}
    grid = spot_ladder(spot, span=span, step=step)
    strikes = df["strike"].to_numpy(float)
    sig = df["iv"].to_numpy(float)
    t = _years(df, book_day)
    w = df["sign"].to_numpy(float) * df["oi"].to_numpy(float) * 100.0 * 0.01
    pts = np.append(grid, spot)
    raw = np.vstack([bs_gamma(s_, strikes, sig, t) for s_ in pts])  # (n_pts, n_contracts)
    contrib = raw * w[None, :] * (pts[:, None] ** 2) / 1e9  # $bn per 1%
    buckets = df["bucket"].to_numpy()
    total = contrib.sum(axis=1)
    out_b: dict[str, dict] = {}
    for key, label in BUCKETS:
        m = buckets == key
        curve = contrib[:, m].sum(axis=1) if m.any() else np.zeros(len(pts))
        exps = sorted(df.loc[m, "expiry"].unique())
        out_b[key] = {
            "label": label,
            "curve": np.round(curve[:-1], 4).tolist(),
            "at_spot": round(float(curve[-1]), 4),
            "zero_cross": zero_cross(grid, curve[:-1], spot),
            "n_expiries": len(exps),
            "expiries": [e.isoformat() for e in exps],
            "sessions": (
                [int(df.loc[m, "sessions"].min()), int(df.loc[m, "sessions"].max())]
                if m.any()
                else None
            ),
        }
    tot_spot = float(total[-1])
    abs_sum = sum(abs(b["at_spot"]) for b in out_b.values()) or 0.0
    for b in out_b.values():
        b["share_pct"] = _share(b["at_spot"], tot_spot)
        b["share_abs_pct"] = round(100.0 * abs(b["at_spot"]) / abs_sum, 1) if abs_sum else None
    by_exp = pd.DataFrame({"expiry": df["expiry"], "g": contrib[-1]}).groupby("expiry")["g"].sum()
    exp_rows = [
        {
            "expiry": e.isoformat(),
            "sessions": sessions_to_expiry(book_day, e),
            "bucket": bucket_for(sessions_to_expiry(book_day, e)),
            "at_spot": round(float(g), 4),
        }
        for e, g in by_exp.items()
    ]
    max_exp = max(df["expiry"])
    return {
        "units": GAMMA_UNITS,
        "spot": float(spot),
        "grid": grid.tolist(),
        "total": {
            "curve": np.round(total[:-1], 4).tolist(),
            "at_spot": round(tot_spot, 4),
            "zero_cross": zero_cross(grid, total[:-1], spot),
        },
        "buckets": out_b,
        "by_expiry": exp_rows,
        "max_expiry": max_exp.isoformat(),
        "max_dte": int((max_exp - book_day).days),
        "n_contracts": len(df),
        "note": DISCLAIMER,
    }


def tenor_vega(chain: pd.DataFrame, spot: float, book_day: date) -> dict:
    """Dealer $vega per vol point at spot by bucket and expiry, under three sign conventions."""
    df = prepare_chain(chain, book_day)
    if df.empty or not np.isfinite(spot) or spot <= 0:
        return {}
    v = bs_vega(spot, df["strike"].to_numpy(float), df["iv"].to_numpy(float), _years(df, book_day))
    gross = v * df["oi"].to_numpy(float) * 100.0 * 0.01 / 1e6  # $mn per vol point, unsigned
    is_call = df["sign"].to_numpy(float) > 0
    frame = pd.DataFrame(
        {
            "bucket": df["bucket"],
            "expiry": df["expiry"],
            "call": np.where(is_call, gross, 0.0),
            "put": np.where(is_call, 0.0, gross),
        }
    )

    def conv(c: float, p: float) -> dict:
        return {
            "call_vega": round(c, 3),
            "put_vega": round(p, 3),
            "naive": round(c - p, 3),
            "short_all": round(-(c + p), 3),
            "flipped": round(p - c, 3),
        }

    out_b = {}
    for key, label in BUCKETS:
        sub = frame[frame["bucket"] == key]
        out_b[key] = {"label": label, **conv(float(sub["call"].sum()), float(sub["put"].sum()))}
    by_exp = frame.groupby("expiry")[["call", "put"]].sum()
    exp_rows = [
        {
            "expiry": e.isoformat(),
            "bucket": bucket_for(sessions_to_expiry(book_day, e)),
            **conv(float(r.call), float(r.put)),
        }
        for e, r in by_exp.iterrows()
    ]
    c_all, p_all = float(frame["call"].sum()), float(frame["put"].sum())
    return {
        "units": VEGA_UNITS,
        "spot": float(spot),
        "buckets": out_b,
        "total": conv(c_all, p_all),
        "by_expiry": exp_rows,
        "conventions": {
            "naive": "dealers long calls, short puts (= customers long puts, short calls)",
            "short_all": "customers long every option: dealers short calls and puts",
            "flipped": "customers long calls, short puts: the naive book times -1",
        },
        "note": DISCLAIMER,
    }
