"""Day-over-day change panels for the vol-surface dashboard.

Reads the most recent ``greeks_chain`` snapshots for a symbol and produces the
markdown "vol changes" / "fixed-strike vol changes" sections (the diff-vs-prior
columns of the ^SPX Volatility Surface reference). These light up once the
``chain_snapshot`` collector has accumulated >= 2 daily snapshots.

Read-through / regime descriptor only — no signals (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.errors import ComputationError
from trading_intel.greeks.surface_changes import (
    atm_term_changes,
    fixed_strike_changes,
    format_atm_changes_markdown,
    format_fixed_strike_changes_markdown,
)
from trading_intel.memory.models import GreeksChain


def _rows_to_chain(rows: list[GreeksChain]) -> pd.DataFrame:
    """Map ``greeks_chain`` ORM rows to the normalized chain the surface uses."""
    return pd.DataFrame(
        [
            {
                "expiration": pd.Timestamp(r.expiry),
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "strike": r.strike,
                "delta": r.delta,
                "iv": r.iv,
            }
            for r in rows
        ]
    )


def load_recent_chain_snapshots(
    session: Session, symbol: str, *, n: int = 2
) -> list[tuple[datetime, pd.DataFrame]]:
    """Return the latest ``n`` distinct chain snapshots for ``symbol``, newest first.

    Each item is ``(ts, chain_df)`` where ``chain_df`` carries the columns the
    surface builders expect (expiration/opt_kind/strike/delta/iv).
    """
    ts_list = list(
        session.execute(
            select(GreeksChain.ts)
            .where(GreeksChain.symbol == symbol)
            .distinct()
            .order_by(GreeksChain.ts.desc())
            .limit(n)
        ).scalars()
    )
    snaps: list[tuple[datetime, pd.DataFrame]] = []
    for ts in ts_list:
        rows = list(
            session.execute(
                select(GreeksChain).where(
                    GreeksChain.symbol == symbol, GreeksChain.ts == ts
                )
            ).scalars()
        )
        snaps.append((ts, _rows_to_chain(rows)))
    return snaps


def build_change_report(session: Session, symbol: str, *, n_expiries: int = 3) -> str:
    """Markdown for the day-over-day change panels (or a 'need more history' note)."""
    snaps = load_recent_chain_snapshots(session, symbol, n=2)
    if len(snaps) < 2:
        return (
            "## Day-over-day vol changes\n"
            f"Not enough history yet — need >= 2 daily snapshots (have {len(snaps)}). "
            "The chain_snapshot collector populates these each session."
        )

    (ts_curr, curr), (ts_prev, prev) = snaps[0], snaps[1]
    sections = [
        f"## Day-over-day vol changes ({ts_prev.date()} -> {ts_curr.date()})",
    ]
    try:
        sections.append(format_fixed_strike_changes_markdown(fixed_strike_changes(prev, curr)))
    except ComputationError as exc:
        sections.append(f"## Fixed-strike vol changes (sticky-strike)\nUnavailable: {exc}")
    try:
        atm = atm_term_changes(prev, curr, n_expiries=n_expiries)
        sections.append(format_atm_changes_markdown(atm))
    except ComputationError as exc:
        sections.append(f"## ATM vol changes (sticky-delta)\nUnavailable: {exc}")
    return "\n\n".join(sections)


def load_fixed_strike_changes(session: Session, symbol: str) -> pd.DataFrame | None:
    """Fixed-strike IV-change frame (curr - prev) for charting, or None.

    Returns the ``fixed_strike_changes`` output (columns ``expiration``,
    ``strike``, ``opt_kind``, ``iv_prev``, ``iv_curr``, ``d_iv_pts``) for the two
    most recent chain snapshots. ``None`` when there are < 2 snapshots or no
    overlapping strikes. Regime descriptor only (FlashAlpha rule 4).
    """
    snaps = load_recent_chain_snapshots(session, symbol, n=2)
    if len(snaps) < 2:
        return None
    (_, curr), (_, prev) = snaps[0], snaps[1]
    try:
        return fixed_strike_changes(prev, curr)
    except ComputationError:
        return None


def fixed_strike_change_matrix(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Pivot fixed-strike ΔIV to ``index=strike, columns=expiration, values=d_iv_pts``.

    Aggregates the call + put fixed-strike IV change at each (strike, expiry) by
    mean so the panel can show the full strike x expiry structure of the
    day-over-day IV move as one diverging heatmap — far more legible than a single
    front-expiry bar. ``None``/empty in -> empty out. Regime descriptor only
    (FlashAlpha rule 4).
    """
    if frame is None or frame.empty or "d_iv_pts" not in frame.columns:
        return pd.DataFrame()
    matrix = frame.pivot_table(
        index="strike", columns="expiration", values="d_iv_pts", aggfunc="mean"
    )
    return matrix.sort_index().sort_index(axis=1)
