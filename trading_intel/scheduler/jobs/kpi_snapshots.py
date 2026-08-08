"""Scheduled job: earnings-call transcript -> ``kpi_snapshots`` (NRR/cRPO/margin).

For each symbol, pulls the latest transcript via the CVForge FMP passthrough
(``earning-call-transcript``) and extracts the operating KPIs the swing-dossier
scorecard needs with a local Ollama pass (``earnings.kpi_extract`` — rule 7).
Idempotent upsert on (symbol, period_label) (rule 5); descriptive only (rule 4).
Best called with an explicit symbol list (the week's reporters) — the Sunday
orchestrator scopes it — but defaults to the watchlist.

Manual run:
    python -m trading_intel.scheduler.jobs.kpi_snapshots NET CSCO
"""

from __future__ import annotations

import sys
import uuid
from datetime import date

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.cvforge import CVForgeClient
from trading_intel.config import Settings, get_settings
from trading_intel.earnings.kpi_extract import extract_kpis
from trading_intel.errors import DataSourceError
from trading_intel.memory.models import KpiSnapshot, Ticker
from trading_intel.synthesis.llm import LLMProvider, OllamaProvider
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_UPDATE = (
    "ts",
    "dbnrr_pct",
    "revenue_growth_yoy_pct",
    "gross_margin_pct",
    "operating_margin_pct",
    "crpo_growth_yoy_pct",
    "rpo_growth_yoy_pct",
    "customers_over_100k",
    "customers_over_1m",
    "fcf_margin_pct",
    "guidance_direction",
    "one_line_kpi_read",
    "source",
)
# a row is worth banking only if at least one headline KPI was extracted
_MEANINGFUL = ("dbnrr_pct", "revenue_growth_yoy_pct", "gross_margin_pct", "crpo_growth_yoy_pct")


def _latest_transcript(client: CVForgeClient, sym: str) -> tuple[str | None, str | None]:
    """``(period_label, text)`` for the most recent transcript, else ``(None, None)``.

    The bare ``earning-call-transcript?symbol`` route 502s upstream (needs
    year+quarter), so we resolve the newest available quarter via
    ``earning-call-transcript-dates`` first. Degrades to ``(None, None)``.
    """
    try:
        dates = client.fmp("earning-call-transcript-dates", {"symbol": sym})
    except DataSourceError:
        dates = None

    year = quarter = None
    if isinstance(dates, list) and dates:
        row = dates[0]
        if isinstance(row, dict):
            year = row.get("fiscalYear") or row.get("year")
            quarter = row.get("quarter") or row.get("period")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            quarter, year = row[0], row[1]
    if not (year and quarter):
        return None, None

    try:
        payload = client.fmp(
            "earning-call-transcript", {"symbol": sym, "year": year, "quarter": quarter}
        )
    except DataSourceError:
        return None, None
    rec = (
        payload[0]
        if isinstance(payload, list) and payload
        else payload
        if isinstance(payload, dict)
        else None
    )
    if not isinstance(rec, dict):
        return None, None
    text = rec.get("content") or ""
    y = rec.get("year") or year
    q = rec.get("quarter") or quarter
    label = f"{y}Q{q}"
    return (label, text) if text else (None, None)


def run(
    session: Session,
    client: CVForgeClient,
    *,
    settings: Settings | None = None,
    symbols: list[str] | None = None,
    llm: LLMProvider | None = None,
    as_of: date | None = None,
) -> int:
    """Extract + bank KPIs for the universe's latest transcripts. Returns rows."""
    settings = settings or get_settings()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="kpi_snapshots")
    as_of = as_of or eastern_now().date()
    syms = symbols or settings.watchlist_symbols
    llm = llm or OllamaProvider(settings)
    model = getattr(settings, "LLM_TAGGING_MODEL", None)

    rows: list[dict] = []
    for sym in syms:
        label, text = _latest_transcript(client, sym)
        if not text or not label:
            continue
        k = extract_kpis(sym, text, llm, model=model)
        row = k.as_row()
        if not any(row.get(f) is not None for f in _MEANINGFUL):
            continue
        row["symbol"] = row["symbol"][:16]
        row["period_label"] = label[:16]
        row["ts"] = as_of
        row["source"] = "cvforge-fmp+ollama"
        rows.append(row)

    if rows:
        session.execute(
            pg_insert(Ticker)
            .values([{"symbol": r["symbol"]} for r in rows])
            .on_conflict_do_nothing(index_elements=["symbol"])
        )
        stmt = pg_insert(KpiSnapshot).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "period_label"],
            set_={c: stmt.excluded[c] for c in _UPDATE},
        )
        session.execute(stmt)
    session.commit()
    bound.info("kpi_snapshots.done", as_of=as_of.isoformat(), rows=len(rows))
    return len(rows)


def main() -> None:
    from trading_intel.memory.db import make_session_factory

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )
    settings = get_settings()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    client = CVForgeClient(settings)
    session_factory = make_session_factory(settings)
    try:
        with session_factory() as session:
            run(session, client, settings=settings, symbols=args or None)
    finally:
        client.close()


if __name__ == "__main__":
    main()
