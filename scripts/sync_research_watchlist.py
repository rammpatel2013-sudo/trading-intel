"""Sync the company-research drop folder into the dynamic watchlist + price data.

Workflow:
1. Scan ``research/company/`` for new research files (PDF/docx).
2. Run each through the local LLM (Ollama) to extract tickers + rationale, and
   record them in ``watchlist_entries`` (idempotent by content hash).
3. Backfill daily price history for the newly surfaced tickers so they have data
   immediately. From the next collector cycle, they are part of the *effective
   watchlist* and get the full regime-data collection (GEX/DEX, flow, walls).

Run on the laptop (Ollama must be running), with DATABASE_URL pointed at the DB:
    .venv\\Scripts\\python scripts\\sync_research_watchlist.py
    .venv\\Scripts\\python scripts\\sync_research_watchlist.py --dir research\\company
"""
from __future__ import annotations

import argparse
from pathlib import Path

import structlog

from trading_intel.clients.prices import YFinancePriceSource
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.watchlist_ingest import DEFAULT_RESEARCH_DIR, ingest_folder
from trading_intel.scheduler.jobs import quotes_daily
from trading_intel.synthesis.llm import OllamaProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync research folder -> watchlist + prices.")
    parser.add_argument("--dir", default=str(DEFAULT_RESEARCH_DIR), help="Research drop folder")
    parser.add_argument("--model", default=None, help="Ollama model override")
    parser.add_argument(
        "--no-backfill", action="store_true", help="Skip the price backfill for new tickers"
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    settings = get_settings()
    llm = OllamaProvider(settings)
    session_factory = make_session_factory(settings)

    with session_factory() as session:
        stats = ingest_folder(session, llm, research_dir=Path(args.dir), model=args.model)
        new_symbols = stats["new_symbols"]
        print(
            f"Ingested {stats['ingested']} file(s), skipped {stats['skipped']}, "
            f"empty {stats['empty']}, failed {stats['failed']}. "
            f"New tickers: {new_symbols or 'none'}"
        )
        if new_symbols and not args.no_backfill:
            quotes_daily.run(
                session,
                YFinancePriceSource(),
                settings=settings,
                period=settings.QUOTES_BACKFILL_PERIOD,
                symbols=new_symbols,
            )
            print(f"Backfilled price history for: {new_symbols}")
    print("Done. New tickers will join the full collection on the next collector cycle.")


if __name__ == "__main__":
    main()
