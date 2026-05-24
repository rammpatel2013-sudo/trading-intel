"""Nightly job: write the interpretive surface + flow report for each ticker.

For every effective-watchlist symbol that has a stored ``oi_chain_eod`` snapshot,
generates the 3-part narrative surface + flow report (surface metrics + stored
option flow + KB grounding) via the LLM (Ollama) and upserts it into
``surface_reports`` (one row per symbol/day). Runs on the LAPTOP (Ollama is not on
the NAS); writes to the NAS Postgres. Overnight scheduling keeps the slow CPU
Ollama generation off the dashboard's page-load path. Descriptive regime
read-through only - FlashAlpha rule 4.

Manual run:
    python -m trading_intel.scheduler.jobs.surface_reports [--symbol AAPL] [--no-llm]
"""
from __future__ import annotations

import re
from datetime import date, datetime

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.dashboard.report_data import generate_surface_flow_report
from trading_intel.dashboard.vol_lab_data import latest_spx_chain
from trading_intel.memory.models import SurfaceReport
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.watchlist import effective_symbols

log = structlog.get_logger(__name__)

_FLOW_SOURCE_RE = re.compile(r"flow source:\s*(.+?)\*", re.IGNORECASE)

# generate_surface_flow_report returns a single italic ``_..._`` sentinel line when
# the surface can't be built (no snapshot / metrics error). Don't persist those.
_SENTINELS = ("No stored oi_chain_eod snapshot", "Surface unavailable")


def _is_sentinel(report_md: str) -> bool:
    head = (report_md or "").lstrip("_")[:80]
    return any(s in head for s in _SENTINELS)


def _flow_source(report_md: str) -> str | None:
    """Pull the flow-source tag out of the report header (best-effort)."""
    m = _FLOW_SOURCE_RE.search(report_md or "")
    if not m:
        return None
    src = m.group(1).strip()
    return None if src.lower() == "none" else src[:32]


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Write/refresh today's surface + flow report for each watchlist ticker."""
    settings = settings or get_settings()
    symbols = symbols or effective_symbols(session, settings)
    as_of = date.today()
    written = 0
    skipped = 0
    for sym in symbols:
        log.info("surface_reports.symbol_start", symbol=sym)
        if latest_spx_chain(session, symbol=sym) is None:
            skipped += 1
            log.info("surface_reports.no_snapshot", symbol=sym)
            continue
        # Overnight there's no live market, so prefer_live=False -> stored flow.
        report = generate_surface_flow_report(
            session, sym, settings=settings, llm=llm, prefer_live=False
        )
        if _is_sentinel(report):
            skipped += 1
            log.info("surface_reports.skipped_no_surface", symbol=sym)
            continue
        model = settings.LLM_DAILY_MODEL if llm is not None else None
        now = datetime.utcnow()
        stmt = pg_insert(SurfaceReport).values(
            symbol=sym, as_of=as_of, report_md=report,
            flow_source=_flow_source(report), model=model, created_at=now,
        ).on_conflict_do_update(
            index_elements=["symbol", "as_of"],
            set_={
                "report_md": report, "flow_source": _flow_source(report),
                "model": model, "created_at": now,
            },
        )
        session.execute(stmt)
        written += 1
        log.info("surface_reports.wrote", symbol=sym)
    session.commit()
    log.info("surface_reports.done", written=written, skipped=skipped)


def main() -> None:
    import argparse

    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    parser = argparse.ArgumentParser(description="Write nightly surface + flow reports.")
    parser.add_argument("--symbol", default=None, help="Only this symbol (else all effective tickers)")
    parser.add_argument("--no-llm", action="store_true", help="Skip Ollama; deterministic report (fast)")
    args = parser.parse_args()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    llm = None if args.no_llm else OllamaProvider(settings)
    with make_session_factory(settings)() as session:
        run(session, settings=settings, llm=llm, symbols=[args.symbol] if args.symbol else None)


if __name__ == "__main__":
    main()
