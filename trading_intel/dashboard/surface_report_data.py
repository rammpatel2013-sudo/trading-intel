"""Reader for stored nightly surface + flow reports (Vol Lab page).

Thin Session query over ``surface_reports`` (written nightly by the
surface-report job). Descriptive regime read-through only (FlashAlpha rule 4).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_intel.memory.models import SurfaceReport


def latest_surface_report(session: Session, symbol: str) -> SurfaceReport | None:
    """Most recent stored surface + flow report for ``symbol`` (or None)."""
    return session.execute(
        select(SurfaceReport)
        .where(SurfaceReport.symbol == symbol)
        .order_by(SurfaceReport.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
