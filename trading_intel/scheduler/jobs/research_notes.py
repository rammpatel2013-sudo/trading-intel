"""Nightly job: write a narrative research note for each research-watchlist ticker.

For every active research-watchlist ticker, gathers the uploaded research-PDF
text, the latest SEC 10-K (EDGAR), FMP profile / financials / news, and the live
options-vol regime, then writes a narrative via the LLM (Ollama) and upserts it
into ``research_notes`` (one row per symbol/day). Runs on the LAPTOP (Ollama is
not on the NAS); writes to the NAS Postgres. Descriptive research read-through
only - FlashAlpha rule 4.

Manual run:
    python -m trading_intel.scheduler.jobs.research_notes [--symbol AAPL]
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.edgar import EdgarClient
from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import Document, ResearchNote, WatchlistEntry
from trading_intel.memory.pdf_pipeline import extract_text
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.research_note import build_research_note
from trading_intel.watchlist import research_symbols

log = structlog.get_logger(__name__)
_PDF_MAX = 3000
_TENK_MAX = 3000


def _ticker_excerpt(text: str, ticker: str, *, window: int = 1800, max_chars: int = 4000) -> str:
    """Return the part(s) of a (possibly multi-ticker) doc that mention ``ticker``.

    A research report can cover many names; the per-ticker note must use the
    section about THIS ticker, not the document's first page. Gathers merged
    windows around each whole-word mention; falls back to the start if none found.
    """
    if not text:
        return ""
    pat = re.compile(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])")
    spans: list[list[int]] = []
    for m in pat.finditer(text):
        s = max(0, m.start() - window // 4)
        e = min(len(text), m.start() + window)
        if spans and s <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    if not spans:
        return text[:max_chars]
    out: list[str] = []
    total = 0
    for s, e in spans:
        out.append(text[s:e])
        total += e - s
        if total >= max_chars:
            break
    return " … ".join(out)[:max_chars]


def _pdf_text_for_symbol(session: Session, symbol: str) -> str:
    """Excerpt (around the ticker's mentions) of the latest research doc that surfaced it."""
    doc_id = session.execute(
        select(WatchlistEntry.source_doc_id)
        .where(WatchlistEntry.symbol == symbol, WatchlistEntry.active.is_(True))
        .order_by(WatchlistEntry.added_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if doc_id is None:
        return ""
    doc = session.get(Document, doc_id)
    if doc is None or not doc.path:
        return ""
    try:
        text, _ = extract_text(Path(doc.path))
        return _ticker_excerpt(text or "", symbol)
    except Exception as exc:  # missing file / parse error
        log.warning("research_notes.pdf_extract_failed", symbol=symbol, error=str(exc))
        return ""


def _regime_md(session: Session, symbol: str) -> str:
    """Best-effort live gamma-regime line for the note."""
    try:
        from trading_intel.dashboard.gamma_regime_data import latest_spx_gamma_regime

        gr = latest_spx_gamma_regime(session, symbol=symbol)
    except Exception:
        gr = None
    if gr is None:
        return "(no live regime data yet)"
    flip = f"{gr.flip:.0f}" if gr.flip is not None else "n/a"
    return f"Gamma regime: {gr.regime} (net GEX {gr.net_gex:,.0f}, flip {flip}). {gr.regime_read()}"


def run(
    session: Session,
    *,
    settings: Settings | None = None,
    llm: LLMProvider | None = None,
    symbols: list[str] | None = None,
) -> None:
    """Write/refresh today's research note for each research-watchlist ticker."""
    settings = settings or get_settings()
    symbols = symbols or research_symbols(session)
    edgar = EdgarClient(user_agent=settings.EDGAR_USER_AGENT)
    as_of = date.today()
    written = 0
    for sym in symbols:
        log.info("research_notes.symbol_start", symbol=sym)
        pdf_text = _pdf_text_for_symbol(session, sym)
        tenk = (edgar.latest_10k(sym) or {}).get("text", "")
        regime_md = _regime_md(session, sym)
        log.info(
            "research_notes.generating", symbol=sym, llm=llm is not None,
            has_pdf=bool(pdf_text), has_10k=bool(tenk),
        )
        note = build_research_note(
            sym, llm=llm, pdf_text=pdf_text, tenk_text=tenk, regime_md=regime_md,
            model=settings.LLM_DAILY_MODEL, max_pdf=_PDF_MAX, max_tenk=_TENK_MAX,
        )
        srcs = ",".join(
            name for name, ok in (
                ("pdf", bool(pdf_text)), ("10-K", bool(tenk)),
                ("regime", "no live regime" not in regime_md),
            ) if ok
        )
        model = settings.LLM_DAILY_MODEL if llm is not None else None
        now = datetime.utcnow()
        stmt = pg_insert(ResearchNote).values(
            symbol=sym, as_of=as_of, note_md=note, sources=srcs or None, model=model, created_at=now,
        ).on_conflict_do_update(
            index_elements=["symbol", "as_of"],
            set_={"note_md": note, "sources": srcs or None, "model": model, "created_at": now},
        )
        session.execute(stmt)
        written += 1
        log.info("research_notes.wrote", symbol=sym, sources=srcs)
    session.commit()
    log.info("research_notes.done", written=written)


def main() -> None:
    import argparse

    from trading_intel.memory.db import make_session_factory
    from trading_intel.synthesis.llm import OllamaProvider

    parser = argparse.ArgumentParser(description="Write nightly research notes.")
    parser.add_argument("--symbol", default=None, help="Only this symbol (else all research tickers)")
    parser.add_argument("--no-llm", action="store_true", help="Skip Ollama; deterministic note (fast)")
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
