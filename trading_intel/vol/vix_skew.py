"""VIX-options skew + OI distribution analytics.

The VIX options chain carries a *call* skew — the opposite of equity put skew —
because tail-risk hedgers bid up OTM VIX calls. The structure of that call wing
(how rich vs the ATM, how concentrated the OI is in OTM calls, how the term
behaves) is a direct read on systemic-tail-hedging demand.

Pure functions over a normalized VIX-options chain DataFrame (the same column
contract as ``OptionsDataSource.chain``: ``opt_kind`` (call/put), ``delta``,
``iv``, ``oi``, ``volume``, ``strike``, ``expiration``). The job layer is
responsible for picking the relevant expiry slice; these helpers operate on
whatever frame they're given.

ADR-003 §3.4: feeds the composite ``vix_tail_hedging_score`` column on
``index_skew_daily`` (z-sum of VIX-call skew, OTM-call OI share, VVIX/VIX).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Default OTM-call delta threshold used when reasoning about "OTM call wing".
#: Convex returns delta as a signed fraction (calls positive, puts negative);
#: 0.30 is the conventional dividing line between near-the-money and OTM.
OTM_DELTA_CUTOFF = 0.30

#: Default target |delta| for the wing-IV read (matches the equity 25Δ choice).
DEFAULT_WING_DELTA = 0.25


def _nearest_expiry(chain: pd.DataFrame) -> pd.Timestamp | None:
    """Earliest expiration in the chain; ``None`` for an empty chain."""
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return None
    exps = pd.to_datetime(chain["expiration"], errors="coerce").dropna()
    if exps.empty:
        return None
    return exps.min()


def _expiry_slice(chain: pd.DataFrame, expiry: pd.Timestamp) -> pd.DataFrame:
    """Sub-frame restricted to one expiration."""
    exp = pd.to_datetime(chain["expiration"], errors="coerce")
    return chain.loc[exp == expiry]


def _atm_iv(slc: pd.DataFrame) -> float | None:
    """ATM IV on one expiry slice: IV at the row with |delta| closest to 0.50."""
    if slc.empty or "iv" not in slc.columns or "delta" not in slc.columns:
        return None
    delta = pd.to_numeric(slc["delta"], errors="coerce").abs()
    iv = pd.to_numeric(slc["iv"], errors="coerce")
    mask = delta.notna() & iv.notna() & (iv > 0)
    if not mask.any():
        return None
    idx = (delta[mask] - 0.50).abs().idxmin()
    val = float(iv.loc[idx])
    return val if np.isfinite(val) else None


def _wing_iv(slc: pd.DataFrame, *, opt_kind: str, abs_delta: float) -> float | None:
    """IV at the requested |delta| on the requested wing (call/put) of one expiry."""
    if slc.empty:
        return None
    side = slc.loc[slc["opt_kind"].astype(str).str.lower().str.startswith(opt_kind[0])]
    if side.empty:
        return None
    delta = pd.to_numeric(side["delta"], errors="coerce").abs()
    iv = pd.to_numeric(side["iv"], errors="coerce")
    mask = delta.notna() & iv.notna() & (iv > 0)
    if not mask.any():
        return None
    idx = (delta[mask] - abs_delta).abs().idxmin()
    val = float(iv.loc[idx])
    return val if np.isfinite(val) else None


def vix_call_wing_iv(
    chain: pd.DataFrame,
    *,
    abs_delta: float = DEFAULT_WING_DELTA,
    expiry: pd.Timestamp | None = None,
) -> float | None:
    """25Δ (default) VIX-call IV at the nearest (or specified) expiry.

    ``None`` if the chain has no usable call row at that delta — the caller
    leaves the column NULL rather than guess.
    """
    exp = expiry or _nearest_expiry(chain)
    if exp is None:
        return None
    slc = _expiry_slice(chain, exp)
    return _wing_iv(slc, opt_kind="call", abs_delta=abs_delta)


def vix_call_skew(
    chain: pd.DataFrame,
    *,
    abs_delta: float = DEFAULT_WING_DELTA,
    expiry: pd.Timestamp | None = None,
) -> float | None:
    """``iv_call_Δ - iv_atm`` at one expiry (positive = call wing rich vs ATM).

    Mirrors the FX-convention butterfly anchoring (vs ATM) but on the call wing
    only — the structurally rich side of a VIX surface.
    """
    exp = expiry or _nearest_expiry(chain)
    if exp is None:
        return None
    slc = _expiry_slice(chain, exp)
    atm = _atm_iv(slc)
    wing = _wing_iv(slc, opt_kind="call", abs_delta=abs_delta)
    if atm is None or wing is None:
        return None
    return wing - atm


def vix_term_call_skew(
    chain: pd.DataFrame,
    *,
    abs_delta: float = DEFAULT_WING_DELTA,
    n_expiries: int = 3,
) -> list[tuple[pd.Timestamp, float]]:
    """Call-wing skew (``iv_call_Δ - iv_atm``) across the nearest N expiries.

    Sorted ascending by expiry. Skips any expiry where the wing or ATM is
    unavailable. Empty list when the chain is empty / malformed.
    """
    if chain is None or chain.empty or "expiration" not in chain.columns:
        return []
    exps = (
        pd.to_datetime(chain["expiration"], errors="coerce")
        .dropna()
        .drop_duplicates()
        .sort_values()
    )
    out: list[tuple[pd.Timestamp, float]] = []
    for exp in exps.iloc[:n_expiries]:
        val = vix_call_skew(chain, abs_delta=abs_delta, expiry=exp)
        if val is not None:
            out.append((exp, val))
    return out


def vix_call_oi_share(
    chain: pd.DataFrame,
    *,
    otm_delta_cutoff: float = OTM_DELTA_CUTOFF,
    expiry: pd.Timestamp | None = None,
) -> float | None:
    """Fraction of OI sitting in OTM calls (``|delta| ≤ cutoff``).

    Hedger-positioning proxy: rising OTM-call OI = more tail-insurance demand.
    Computed across all expiries by default; restrict with ``expiry`` for a
    single-tenor read. ``None`` if the chain has no usable OI rows.
    """
    if chain is None or chain.empty:
        return None
    df = chain.copy()
    if expiry is not None:
        df = _expiry_slice(df, expiry)
    if df.empty:
        return None
    if not {"opt_kind", "delta", "oi"}.issubset(df.columns):
        return None
    oi = pd.to_numeric(df["oi"], errors="coerce")
    delta = pd.to_numeric(df["delta"], errors="coerce")
    kind = df["opt_kind"].astype(str).str.lower()
    total_oi = float(oi.dropna().sum())
    if total_oi <= 0:
        return None
    otm_call_mask = kind.str.startswith("c") & (delta.abs() <= otm_delta_cutoff) & oi.notna()
    otm_call_oi = float(oi.loc[otm_call_mask].sum())
    return otm_call_oi / total_oi


def vix_call_premium_share(
    chain: pd.DataFrame,
    *,
    otm_delta_cutoff: float = OTM_DELTA_CUTOFF,
    expiry: pd.Timestamp | None = None,
) -> float | None:
    """Notional-weighted version of ``vix_call_oi_share`` — share of $-volume in OTM calls.

    Uses ``volume * iv`` as a cheap notional proxy (true notional needs the
    spot+price; volume*IV is monotone in the same direction and avoids a join
    against the underlying). ``None`` when the chain lacks the columns or no
    volume.
    """
    if chain is None or chain.empty:
        return None
    df = chain.copy()
    if expiry is not None:
        df = _expiry_slice(df, expiry)
    if df.empty:
        return None
    if not {"opt_kind", "delta", "volume", "iv"}.issubset(df.columns):
        return None
    vol = pd.to_numeric(df["volume"], errors="coerce")
    iv = pd.to_numeric(df["iv"], errors="coerce")
    delta = pd.to_numeric(df["delta"], errors="coerce")
    kind = df["opt_kind"].astype(str).str.lower()
    notional = (vol * iv).where(vol.notna() & iv.notna() & (vol > 0))
    total = float(notional.dropna().sum())
    if total <= 0:
        return None
    otm_call_mask = (
        kind.str.startswith("c")
        & (delta.abs() <= otm_delta_cutoff)
        & notional.notna()
    )
    return float(notional.loc[otm_call_mask].sum()) / total


def vix_tail_hedging_score(
    *,
    call_skew_z: float | None,
    oi_share_z: float | None,
    vvix_vix_z: float | None,
) -> float | None:
    """Composite z-sum of three independent tail-hedging proxies.

    Three pre-standardized z-scores in, one number out. ``None`` when ALL three
    inputs are missing; otherwise we sum the available components (the missing
    ones implicitly = 0 z-score) and return that sum. The job that calls this is
    responsible for the z-score standardization against trailing history.
    """
    parts = [v for v in (call_skew_z, oi_share_z, vvix_vix_z) if v is not None and np.isfinite(v)]
    if not parts:
        return None
    return float(sum(parts))
