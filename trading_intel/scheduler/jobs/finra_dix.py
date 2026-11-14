"""Own DIX from FINRA short volume (2026-11-13 overnight; Phase 6, Mithil kept it 11/13).

DIX (SqueezeMetrics' idea) = dollar-weighted short-volume share of S&P 500 names: off-exchange
short volume is mostly market makers filling BUY orders, so a high DIX = buying in dark pools.
Ours: sum(short_vol x price) / sum(total_vol x price) over today's S&P 500 constituents, prices
from FMP batch-quote (~6 calls). Idempotent upsert into ``finra_dix_daily``. NAS DSM Mon-Fri
18:30 ET (`run_job.sh finra_dix`). Compare with the stored SqueezeMetrics DIX before trusting.
Descriptor only (rule 4).
"""
from __future__ import annotations

import uuid
from datetime import date

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.config import Settings, get_settings
from trading_intel.memory.models import FinraDixDaily
from trading_intel.timeutils import eastern_now, is_trading_session

log = structlog.get_logger(__name__)


def compute(short: dict[str, tuple[float, float]], prices: dict[str, float]) -> dict:
    sv = tv = 0.0
    n = 0
    for sym, px in prices.items():
        s = short.get(sym)
        if not s or not px or s[1] <= 0:
            continue
        sv += s[0] * px
        tv += s[1] * px
        n += 1
    return {"dix": (sv / tv) if tv else None, "short_dollars": sv, "total_dollars": tv, "n_symbols": n}


def _prices(cv: object, syms: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(syms), 100):
        try:
            data = cv.fmp("batch-quote", {"symbols": ",".join(syms[i:i + 100])})  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - one chunk missing never zeros the day
            log.warning("finra_dix.quote_chunk_failed", error=str(exc))
            continue
        for q in data if isinstance(data, list) else []:
            if isinstance(q, dict) and q.get("symbol") and q.get("price"):
                out[str(q["symbol"]).upper()] = float(q["price"])
    return out


def run(session: Session, settings: Settings | None = None, *, cv: object | None = None,
        short: dict | None = None, day: date | None = None) -> dict:
    settings = settings or get_settings()
    day = day or eastern_now().date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="finra_dix")
    if not is_trading_session(day):
        return {"skipped": "not_a_trading_session"}
    if short is None:
        from trading_intel.clients.finra import FinraClient
        short = FinraClient().short_volume_all(day)
    if not short:
        bound.warning("finra_dix.no_file_yet", day=str(day))
        return {"skipped": "no_file"}
    if cv is None:
        from trading_intel.clients.cvforge import CVForgeClient
        cv = CVForgeClient(settings)
    from trading_intel.market import breadth as bm
    syms = bm.sp500_symbols(cv)
    vals = compute(short, _prices(cv, syms))
    row = {"date": day, **vals}
    ins = sqlite_insert if (session.bind is not None and session.bind.dialect.name == "sqlite") else pg_insert
    stmt = ins(FinraDixDaily).values(row)
    session.execute(stmt.on_conflict_do_update(index_elements=["date"],
                                               set_={k: stmt.excluded[k] for k in vals}))
    session.commit()
    bound.info("finra_dix.done", **{k: v for k, v in row.items() if k != "date"})
    return row


def main() -> None:
    from trading_intel.memory.db import make_session_factory
    s = get_settings()
    with make_session_factory(s)() as session:
        run(session, s)


if __name__ == "__main__":
    main()
