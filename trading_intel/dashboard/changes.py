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
