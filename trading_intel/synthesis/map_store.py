"""Persistence for the MAP card: bank it in the morning, read it back for grading.

Two directions, both idempotent:

* ``record_morning`` — called by the pre-open brief with the MAP block it just
  built. Upserts the levels half of ``map_daily``. The brief can be re-run any
  number of times; the levels simply get rewritten with the latest pull, and
  the graded half is never touched.
* ``graded_count`` / ``recent_rows`` — what the brief reads back for the
  "Yesterday:" line, the Monday scorecard, and to decide whether the fragility
  badge may stop saying "calibrating".

Descriptor only (FlashAlpha rule 4).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.memory.models import MapDaily

log = structlog.get_logger(__name__)

#: The levels half — the only columns ``record_morning`` is allowed to rewrite.
#: Grading columns are deliberately absent so a re-run of the brief can never
#: wipe a grade that has already been written. ``p_below``/``p_between``/
#: ``p_above`` exist on the table but are no longer written: the three-ways
#: odds left the brief with the N(d2) removal and return with Breeden-
#: Litzenberger.
_MORNING_COLS = (
    "spot", "book_flip", "day_flip", "long_gamma", "net_gamma_dollars",
    "floor", "cap", "day_floor", "day_cap",
    "atm_iv",
    "range70_lo", "range70_hi",
    "frag_tol", "frag_wall_move", "frag_flip_move",
    "walls_firm", "flip_firm", "levels_firm",
)


def _leg(frag: dict[str, Any], name: str) -> dict[str, Any]:
    for item in frag.get("legs") or []:
        if item.get("name") == name:
            return item
    return {}


def _range70(block: dict[str, Any]) -> tuple[float | None, float | None]:
    """The 70% band — the 15Δ cone (build plan §5)."""
    for cone in block.get("cones") or []:
        if cone.get("band_pct") == 70:
            return (cone.get("lower"), cone.get("upper"))
    return (None, None)


def morning_values(block: dict[str, Any]) -> dict[str, Any]:
    """Project a MAP block into the levels half of a ``map_daily`` row. Pure."""
    reg = block.get("regime") or {}
    frag = block.get("fragility") or {}
    walls_leg, flip_leg = _leg(frag, "walls"), _leg(frag, "flip")
    views = block.get("wall_views") or []
    near = views[0] if views else {}
    day = views[1] if len(views) > 1 else {}
    switches = {s.get("name"): s.get("value") for s in (block.get("switches") or [])}
    lo70, hi70 = _range70(block)
    return {
        "spot": block.get("spot"),
        "book_flip": switches.get("whole-book flip"),
        "day_flip": switches.get("same-day flip"),
        "long_gamma": reg.get("long_gamma"),
        "net_gamma_dollars": reg.get("net_gamma_dollars"),
        "floor": near.get("put_wall"),
        "cap": near.get("call_wall"),
        "day_floor": day.get("put_wall"),
        "day_cap": day.get("call_wall"),
        "atm_iv": block.get("atm_iv"),
        "range70_lo": lo70,
        "range70_hi": hi70,
        "frag_tol": frag.get("tol"),
        "frag_wall_move": walls_leg.get("move"),
        "frag_flip_move": flip_leg.get("move"),
        "walls_firm": walls_leg.get("firm"),
        "flip_firm": flip_leg.get("firm"),
        "levels_firm": frag.get("firm"),
    }


def record_morning(
    session: Session, block: dict[str, Any], *, today: date | None = None
) -> bool:
    """Upsert the morning levels for ``today``. Returns True when written.

    Never raises — the brief must render whether or not this lands.
    """
    if not block:
        return False
    today = today or date.today()
    try:
        values = morning_values(block)
        stmt = pg_insert(MapDaily).values(
            symbol=block.get("symbol") or "SPX", date=today, **values
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "date"],
            set_={c: stmt.excluded[c] for c in _MORNING_COLS},
        )
        session.execute(stmt)
        session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — recording is best-effort
        session.rollback()
        log.warning("map_store.record_failed", error=str(exc))
        return False


def graded_count(session: Session, symbol: str = "SPX") -> int:
    """How many sessions have been graded — drives the "calibrating" badge."""
    try:
        return int(
            session.execute(
                select(func.count())
                .select_from(MapDaily)
                .where(MapDaily.symbol == symbol, MapDaily.bucket.isnot(None))
            ).scalar_one()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("map_store.count_failed", error=str(exc))
        return 0


def recent_rows(
    session: Session, symbol: str = "SPX", *, days: int = 20, before: date | None = None
) -> list[dict[str, Any]]:
    """The last ``days`` GRADED rows, newest first, as plain dicts."""
    try:
        q = select(MapDaily).where(MapDaily.symbol == symbol, MapDaily.bucket.isnot(None))
        if before is not None:
            q = q.where(MapDaily.date <= before)
        rows = session.execute(q.order_by(MapDaily.date.desc()).limit(days)).scalars().all()
        return [_as_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        log.warning("map_store.recent_failed", error=str(exc))
        return []


def ungraded_rows(
    session: Session, symbol: str = "SPX", *, since: date | None = None
) -> list[MapDaily]:
    """Rows that have levels but no grade yet — the grading job's work list."""
    q = select(MapDaily).where(MapDaily.symbol == symbol, MapDaily.bucket.is_(None))
    if since is not None:
        q = q.where(MapDaily.date >= since)
    return list(session.execute(q.order_by(MapDaily.date)).scalars().all())


def _as_dict(row: MapDaily) -> dict[str, Any]:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def previous_graded(
    session: Session, symbol: str = "SPX", *, before: date | None = None
) -> dict[str, Any] | None:
    """The most recent graded session — the brief's "Yesterday:" line."""
    before = before or date.today()
    rows = recent_rows(session, symbol, days=1, before=before - timedelta(days=0))
    return rows[0] if rows else None
