"""Database engine / session wiring.

Single place that turns a ``Settings`` object into a SQLAlchemy session
factory. Composition roots (scheduler/runner.py, dashboard/Home.py, and the
manual job entrypoints) call ``make_session_factory`` once and inject the
resulting sessions downstream — no module-level engine globals.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_intel.config import Settings


def make_engine(settings: Settings) -> Engine:
    """Create a SQLAlchemy engine from the configured DATABASE_URL."""
    return create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    """Return a configured ``sessionmaker`` bound to a fresh engine."""
    return sessionmaker(bind=make_engine(settings), expire_on_commit=False, future=True)
