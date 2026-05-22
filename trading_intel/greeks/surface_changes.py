"""Day-over-day volatility-surface changes (sticky-strike vs sticky-delta).

Two read-throughs comparing two per-strike chain snapshots (e.g. yesterday's vs
today's ``greeks_chain`` rows):

- ``fixed_strike_changes`` — IV change at each literal (expiry, strike, side).
  The *sticky-strike* view: how the surface repriced at fixed strikes.
- ``atm_term_changes`` — ATM IV change per expiry on the delta (floating)
  surface. The *sticky-delta* view.

Comparing the two tells you which regime the surface is in (strikes repricing
vs the smile sliding with spot). Regime descriptor only — emits no signals
(FlashAlpha rule 4). Pure transforms over normalized chain frames; the caller
supplies the two snapshots.
"""

from __future__ import annotations

import pandas as pd

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface import build_delta_surface

_FS_REQUIRED = ("expiration", "strike", "opt_kind", "iv")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["expiration", "strike", "opt_kind", "iv"]].copy()
    out["expiration"] = pd.to_datetime(out["expiration"], errors="coerce").dt.date
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    out["opt_kind"] = out["opt_kind"].astype(str).str.upper().str[0]
    out["iv"] = pd.to_numeric(out["iv"], errors="coerce")
    return out.dropna(subset=["expiration", "strike", "opt_kind", "iv"])


def fixed_strike_changes(prev: pd.DataFrame, curr: pd.DataFrame) -> pd.DataFrame:
    """Per (expiry, strike, side) IV change in vol points (curr - prev).

    Inner-joins the two snapshots on (expiration, strike, opt_kind), so only
    strikes present on BOTH days appear. ``d_iv_pts`` is the change in vol
    points (e.g. +1.5 = +1.5 vol pts). Rows are sorted by ``|d_iv_pts|``
    descending (biggest movers first).
    """
    for name, df in (("prev", prev), ("curr", curr)):
        if df is None or df.empty:
            raise ComputationError(f"Empty {name} snapshot: cannot diff fixed-strike vol")
        missing = [c for c in _FS_REQUIRED if c not in df.columns]
        if missing:
            raise ComputationError(f"{name} snapshot missing columns: {missing}")

    p, c = _normalize(prev), _normalize(curr)
    merged = p.merge(c, on=["expiration", "strike", "opt_kind"], suffixes=("_prev", "_curr"))
    if merged.empty:
        raise ComputationError("No overlapping (expiry, strike, side) between snapshots")
    merged["d_iv_pts"] = (merged["iv_curr"] - merged["iv_prev"]) * 100.0
    merged = merged.sort_values("d_iv_pts", key=lambda s: s.abs(), ascending=False)
    return merged.reset_index(drop=True)


def atm_term_changes(
    prev: pd.DataFrame, curr: pd.DataFrame, *, n_expiries: int = 3
) -> pd.DataFrame:
    """Per-expiry ATM IV change (vol pts) on the delta surface (sticky-delta).

    Builds a delta surface for each snapshot and aligns expiries by date (the
    expiry date is stable across days even as DTE shrinks). Returns columns
    ``expiry``, ``atm_prev``, ``atm_curr``, ``d_atm_pts`` for the expiries common
    to both days.
    """
    sp = build_delta_surface(prev, n_expiries=n_expiries)
    sc = build_delta_surface(curr, n_expiries=n_expiries)
    prev_atm = {sp.expiries[i]: float(sp.atm_iv[i]) for i in range(sp.n_expiries)}
    curr_atm = {sc.expiries[i]: float(sc.atm_iv[i]) for i in range(sc.n_expiries)}

    rows: list[dict] = []
    for exp in sorted(set(prev_atm) & set(curr_atm)):
        rows.append(
            {
                "expiry": exp,
                "atm_prev": prev_atm[exp],
                "atm_curr": curr_atm[exp],
                "d_atm_pts": (curr_atm[exp] - prev_atm[exp]) * 100.0,
            }
        )
    if not rows:
        raise ComputationError("No common expiries between snapshots for ATM change")
    return pd.DataFrame(rows)


def format_fixed_strike_changes_markdown(changes: pd.DataFrame, *, top_n: int = 8) -> str:
    """Render the biggest fixed-strike IV moves as a report sub-section."""
    lines = ["## Fixed-strike vol changes (sticky-strike)"]
    if changes is None or changes.empty:
        lines.append("No overlapping strikes between the last two snapshots.")
        return "\n".join(lines)
    for _, r in changes.head(top_n).iterrows():
        lines.append(
            f"- {r['expiration']} {r['strike']:g}{r['opt_kind']}: "
            f"{r['d_iv_pts']:+.2f} vol pts "
            f"({r['iv_prev'] * 100:.1f}% -> {r['iv_curr'] * 100:.1f}%)"
        )
    return "\n".join(lines)


def format_atm_changes_markdown(changes: pd.DataFrame) -> str:
    """Render per-expiry ATM IV changes as a report sub-section."""
    lines = ["## ATM vol changes (sticky-delta)"]
    if changes is None or changes.empty:
        lines.append("No common expiries between the last two snapshots.")
        return "\n".join(lines)
    for _, r in changes.iterrows():
        lines.append(
            f"- {r['expiry']}: {r['d_atm_pts']:+.2f} vol pts "
            f"({r['atm_prev'] * 100:.1f}% -> {r['atm_curr'] * 100:.1f}%)"
        )
    return "\n".join(lines)
