"""Nightly beta-reset monitor job (Phase 5 B1, 2026-11-04) -> ``beta_reset_daily``.

Free data only — no Convex: SPLV / SPHB / SPX daily closes from FMP via CVForge (via
``clients.fmp_prices``; Yahoo exit 2026-11-14) and Cboe COR3M / COR10D / COR90D index history from the Cboe
CDN (via ``clients.cboe``). Full recompute each run (5 years), upserted per date,
so the first run is the backfill and re-runs are idempotent (rule 5). Maths in
``market/beta_reset.py``. Descriptor only (rule 4). DSM ~17:50 ET.

Manual run:
    python -m trading_intel.scheduler.jobs.beta_reset
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import structlog
from sqlalchemy.orm import Session

from trading_intel.market import beta_reset as br
from trading_intel.memory.models import BetaResetDaily

log = structlog.get_logger(__name__)

PERIOD = "5y"
COR_NAMES = ("COR3M", "COR10D", "COR90D")


def _closes(price_source: Any, symbol: str) -> dict[date, float]:
    df = price_source.daily_history(symbol, period=PERIOD)
    out = {}
    for d, c in zip(df["date"], df["close"]):
        dd = d.date() if hasattr(d, "date") else d
        if c == c and c:
            out[dd] = float(c)
    return out


def _cor(cboe: Any) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for name in COR_NAMES:
        try:
            out[name] = dict(cboe.index_history(name))
        except Exception as exc:  # noqa: BLE001 — context only; never block the job
            log.warning("beta_reset.cor_failed", name=name, error=str(exc))
            out[name] = {}
    return out


def upsert(session: Session, rows: list[br.BetaResetRow]) -> int:
    for r in rows:
        session.merge(
            BetaResetDaily(**{k: getattr(r, k) for k in BetaResetDaily.__table__.columns.keys()})
        )
    session.commit()
    return len(rows)


def run(session: Session, price_source: Any, cboe: Any) -> dict:
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="beta_reset")
    spx = _closes(price_source, "SPX")
    splv = _closes(price_source, "SPLV")
    sphb = _closes(price_source, "SPHB")
    cor = _cor(cboe)
    rows = br.compute(spx, splv, sphb, cor)
    n = upsert(session, rows)
    last = rows[-1] if rows else None
    bound.info(
        "beta_reset.done",
        rows=n,
        last=last.date.isoformat() if last else None,
        label=last.label if last else None,
        cor_rows={k: len(v) for k, v in cor.items()},
    )
    return {"rows": n, "read": br.plain_read(last), "last": last.as_dict() if last else None}


def main() -> None:
    from trading_intel.clients.cboe import CboeClient
    from trading_intel.clients.fmp_prices import fmp_price_source
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.scheduler.jobs._script_report import configure_logging

    configure_logging()
    with make_session_factory(get_settings())() as session:
        out = run(session, fmp_price_source(), CboeClient())
    print(out["read"])


if __name__ == "__main__":
    main()
