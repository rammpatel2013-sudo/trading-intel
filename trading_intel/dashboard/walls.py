"""Call-wall / put-wall history panel for the dashboard.

Reads the per-strike ``greeks_chain`` snapshots for a symbol, computes the
call/put wall for the latest snapshot of each day, and renders the current
walls plus how they moved day over day. Lights up once the ``chain_snapshot``
collector has accumulated >= 2 days. Regime descriptor only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.greeks.walls import compute_walls
from trading_intel.memory.models import GreeksChain


def _rows_to_chain(rows: list[GreeksChain]) -> pd.DataFrame:
    """Map greeks_chain ORM rows to the columns compute_walls needs."""
    return pd.DataFrame(
        [
            {
                "strike": r.strike,
                "opt_kind": "call" if str(r.cp).upper().startswith("C") else "put",
                "gxoi": r.gxoi,
            }
            for r in rows
        ]
    )


def load_wall_history(
    session: Session, symbol: str, *, days: int = 10
) -> list[dict]:
    """Call/put walls for the latest snapshot of each of the last ``days`` days.

    Returns newest-first dicts: ``{date, ts, call_wall, put_wall,
    call_wall_gxoi, put_wall_gxoi}``.
    """
    ts_list = list(
        session.execute(
            select(GreeksChain.ts)
            .where(GreeksChain.symbol == symbol)
            .distinct()
            .order_by(GreeksChain.ts.desc())
        ).scalars()
    )
    # Keep the latest snapshot per calendar day, newest first.
    chosen: list[datetime] = []
    seen: set[date] = set()
    for ts in ts_list:
        d = ts.date()
        if d in seen:
            continue
        seen.add(d)
        chosen.append(ts)
        if len(chosen) >= days:
            break

    history: list[dict] = []
    for ts in chosen:
        rows = list(
            session.execute(
                select(GreeksChain).where(
                    GreeksChain.symbol == symbol, GreeksChain.ts == ts
                )
            ).scalars()
        )
        walls = compute_walls(_rows_to_chain(rows))
        history.append({"date": ts.date(), "ts": ts, **walls})
    return history


def _move(curr: float | None, prev: float | None) -> str:
    if curr is None or prev is None:
        return ""
    delta = curr - prev
    if delta > 0:
        return f" (up {delta:g} from {prev:g})"
    if delta < 0:
        return f" (down {-delta:g} from {prev:g})"
    return " (unchanged)"


def build_wall_report(session: Session, symbol: str, *, days: int = 10) -> str:
    """Markdown for the call/put-wall panel: today's walls + day-over-day move."""
    history = load_wall_history(session, symbol, days=days)
    lines = ["## Call / put walls (gamma-OI)"]
    if not history:
        lines.append("No chain snapshots stored yet.")
        return "\n".join(lines)

    cur = history[0]
    prev = history[1] if len(history) > 1 else None
    cw, pw = cur["call_wall"], cur["put_wall"]
    cw_txt = f"{cw:g}" if cw is not None else "n/a"
    pw_txt = f"{pw:g}" if pw is not None else "n/a"
    cw_move = _move(cw, prev["call_wall"]) if prev else ""
    pw_move = _move(pw, prev["put_wall"]) if prev else ""
    lines.append(
        f"As of {cur['date']}: call wall **{cw_txt}**{cw_move}; "
        f"put wall **{pw_txt}**{pw_move}."
    )

    if len(history) > 1:
        lines.append("")
        lines.append("Recent history (newest first):")
        for h in history:
            c = f"{h['call_wall']:g}" if h["call_wall"] is not None else "n/a"
            p = f"{h['put_wall']:g}" if h["put_wall"] is not None else "n/a"
            lines.append(f"- {h['date']}: call wall {c}, put wall {p}")
    else:
        lines.append("")
        lines.append("Only one day stored so far — movement shows once there are >= 2 days.")
    return "\n".join(lines)


def wall_history_frame(session: Session, symbol: str, *, days: int = 10) -> pd.DataFrame:
    """Call/put walls per day as an ascending frame for plotting drift.

    Columns: ``date``, ``call_wall``, ``put_wall`` (oldest first). Empty frame
    when no snapshots are stored.
    """
    history = load_wall_history(session, symbol, days=days)
    if not history:
        return pd.DataFrame(columns=["date", "call_wall", "put_wall"])
    frame = pd.DataFrame(
        [
            {"date": h["date"], "call_wall": h["call_wall"], "put_wall": h["put_wall"]}
            for h in history
        ]
    )
    return frame.sort_values("date").reset_index(drop=True)
