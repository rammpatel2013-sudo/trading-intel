"""Scheduled job: fund 13F holdings via CVForge FMP -> bank + QoQ diff -> research watchlist.

For each configured 13F filer (``letters.sources.edgar_13f_sources``, keyed by CIK):
pull the latest 13F holdings from the **CVForge FMP passthrough** (tickers included, no
SEC scraping — ADR-004), upsert a ``filing_holdings`` snapshot (idempotent on
cik/period/cusip, rule 5), diff against the prior banked period, and surface the
new/added names onto the RESEARCH watchlist (``watchlist_entries``) — this is how the
LP-only funds get "read".

CAVEAT: ``FMP_13F_ENDPOINT`` + field spellings are best-guess and FMP institutional
endpoints may be paywalled on the CVForge tier (the sentiment collector is parked for
that reason). Confirm both with ``scripts/probe_fmp_13f.py`` before trusting output.
Descriptive research context only, never a signal (rule 4); local/vendor read only (rule 7).

Manual run:
    python -m trading_intel.scheduler.jobs.filings_fetch
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.letters.edgar import Holding, diff_holdings, holdings_from_fmp
from trading_intel.letters.sources import edgar_13f_sources
from trading_intel.memory.models import Document, FilingHolding, WatchlistEntry

log = structlog.get_logger(__name__)

#: CVForge/FMP /stable 13F endpoints (confirmed via scripts/probe_fmp_13f.py 2026-07-19):
#: ``dates`` lists a fund's filings; ``extract`` returns the holdings for one year/quarter.
FMP_13F_DATES = "institutional-ownership/dates"
FMP_13F_EXTRACT = "institutional-ownership/extract"
_SURFACE_KINDS = ("new", "added")


def _as_list(res: object) -> list[dict]:
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for key in ("data", "holdings", "results", "rows"):
            if isinstance(res.get(key), list):
                return res[key]
    return []


def _fetch_latest(client: CVForgeClient, cik: str) -> tuple[list[dict], str]:
    """Holdings rows + period (YYYY-MM-DD) for the fund's newest 13F, else ([], "").

    Two-step: ``dates`` (per-CIK filing list) -> newest year/quarter -> ``extract``.
    """
    dates = _as_list(client.fmp(FMP_13F_DATES, {"cik": str(cik)}))
    if not dates:
        return [], ""
    top = max(dates, key=lambda d: (int(d.get("year") or 0), int(d.get("quarter") or 0)))
    period = str(top.get("date") or "")[:10] or date.today().isoformat()
    rows = _as_list(
        client.fmp(
            FMP_13F_EXTRACT,
            {"cik": str(cik), "year": top.get("year"), "quarter": top.get("quarter")},
        )
    )
    return rows, period


def _prior_holdings(session: Session, cik: str, current_period: str) -> list[Holding]:
    period = session.execute(
        select(FilingHolding.period)
        .where(FilingHolding.cik == cik, FilingHolding.period < current_period)
        .order_by(FilingHolding.period.desc())
        .limit(1)
    ).scalar_one_or_none()
    if period is None:
        return []
    rows = (
        session.execute(
            select(FilingHolding).where(FilingHolding.cik == cik, FilingHolding.period == period)
        )
        .scalars()
        .all()
    )
    return [
        Holding(r.issuer or "", r.cusip, r.value_usd or 0.0, r.shares or 0.0, r.ticker)
        for r in rows
    ]


def _upsert_snapshot(
    session: Session, cik: str, fund: str, period: str, holdings: list[Holding]
) -> None:
    if not holdings:
        return
    now = datetime.utcnow()
    rows = [
        {
            "cik": cik,
            "fund": fund[:64],
            "period": period,
            "cusip": (h.cusip or (h.ticker or ""))[:12],
            "issuer": (h.issuer or None) and h.issuer[:128],
            "ticker": h.ticker,
            "value_usd": h.value_usd,
            "shares": h.shares,
            "fetched_at": now,
        }
        for h in holdings
    ]
    stmt = (
        pg_insert(FilingHolding)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["cik", "period", "cusip"])
    )
    session.execute(stmt)


def _doc_for_filing(session: Session, cik: str, period: str) -> Document:
    sha = hashlib.sha256(f"13f:{cik}:{period}".encode()).hexdigest()
    doc = session.execute(select(Document).where(Document.sha256 == sha)).scalar_one_or_none()
    if doc is not None:
        return doc
    doc = Document(
        path=f"13f/{cik}/{period}", source="cvforge", type="13f", kind="research", sha256=sha
    )
    session.add(doc)
    session.flush()
    return doc


def _surface_watchlist(
    session: Session, doc_id: int, fund: str, period: str, changes: list
) -> list[str]:
    records = []
    surfaced: list[str] = []
    for c in changes:
        if c.kind not in _SURFACE_KINDS or not c.ticker:
            continue
        records.append(
            {
                "symbol": c.ticker[:16],
                "source_doc_id": doc_id,
                "rationale": f"{fund} 13F {period}: {c.kind}, ${c.cur_value:,.0f}",
                "sentiment": None,
                "confidence": None,
                "themes": ["13f", fund],
                "added_at": datetime.utcnow(),
                "active": True,
            }
        )
        surfaced.append(c.ticker)
    if records:
        stmt = (
            pg_insert(WatchlistEntry)
            .values(records)
            .on_conflict_do_nothing(index_elements=["symbol", "source_doc_id"])
        )
        session.execute(stmt)
    return surfaced


def run(session: Session, client: CVForgeClient, *, settings: Settings | None = None) -> dict:
    """Fetch + bank each fund's latest 13F, diff, and surface new/added to the watchlist."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="filings_fetch")
    banked = 0
    all_surfaced: list[str] = []

    for src in edgar_13f_sources():
        try:
            rows, period = _fetch_latest(client, src.ref)
        except (TradingIntelError, KeyError, ValueError, TypeError) as exc:
            bound.warning("filings_fetch.fetch_failed", fund=src.fund, cik=src.ref, err=str(exc))
            continue
        if not rows:
            bound.warning("filings_fetch.empty", fund=src.fund, cik=src.ref)
            continue
        holdings = holdings_from_fmp(rows)
        if not holdings:
            continue
        prior = _prior_holdings(session, src.ref, period)
        _upsert_snapshot(session, src.ref, src.fund, period, holdings)
        # Surface only genuine QoQ new/added. The FIRST (baseline) run has no prior to
        # diff, so it banks the snapshot only — otherwise the whole book floods the
        # watchlist. Real moves surface once a second quarter is banked.
        surfaced: list[str] = []
        if prior:
            changes = diff_holdings(prior, holdings)
            doc = _doc_for_filing(session, src.ref, period)
            surfaced = _surface_watchlist(session, doc.id, src.fund, period, changes)
        session.commit()
        banked += 1
        all_surfaced.extend(surfaced)
        bound.info(
            "filings_fetch.fund_done",
            fund=src.fund,
            period=period,
            holdings=len(holdings),
            surfaced=len(surfaced),
            baseline=not prior,
        )

    bound.info("filings_fetch.done", funds_banked=banked, new_symbols=sorted(set(all_surfaced)))
    return {"funds_banked": banked, "new_symbols": sorted(set(all_surfaced))}


def main() -> None:
    """Manual entrypoint: wire Settings -> CVForge + session, run once."""
    from trading_intel.memory.db import make_session_factory

    settings = get_settings()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    client = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            result = run(session, client, settings=settings)
    finally:
        client.close()
    print(f"Done: {result}")


if __name__ == "__main__":
    main()
