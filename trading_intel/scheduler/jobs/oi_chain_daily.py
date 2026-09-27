"""Scheduled job (EOD, after oi_chain_eod): roll the per-strike chain into ``oi_chain_daily``.

``oi_chain_eod`` is huge (~6M rows) and pruned at ``OI_CHAIN_RETENTION_DAYS``;
``oi_chain_daily`` keeps one row per (symbol, day) FOREVER so the reports can trend
OI / volume / net GEX / walls / ATM IV over years, not months.

One set-based Postgres statement per run: every (symbol, day) present in
``oi_chain_eod`` since ``since`` is aggregated and upserted (rule 5 — re-runs
refresh, never duplicate). The first run backfills the whole surviving window.

Conventions: ``gxoi`` is stored unsigned per side, so ``net_gxoi`` = calls − puts
(dealer-long-calls / short-puts convention, same as the reports). Walls = strike
with the largest same-side gxoi over 0–60 DTE (matches ``get_walls``). Spot =
that day's ``quotes_daily`` close. Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.oi_chain_daily            # last 10 days
    python -m trading_intel.scheduler.jobs.oi_chain_daily --all      # full backfill
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

DEFAULT_LOOKBACK_DAYS = 10

_SQL = text("""
WITH base AS (
    SELECT symbol, ts::date AS d, source, expiry, strike, cp, dte,
           oi, oi_change, volume, gxoi, dxoi, vxoi, iv
    FROM oi_chain_eod
    WHERE ts >= :since
),
spot AS (
    SELECT symbol, date AS d, close FROM quotes_daily WHERE date >= :since_d
),
agg AS (
    SELECT symbol, d, max(source) AS source,
           sum(oi) FILTER (WHERE cp = 'C') AS call_oi,
           sum(oi) FILTER (WHERE cp = 'P') AS put_oi,
           sum(volume) FILTER (WHERE cp = 'C') AS call_volume,
           sum(volume) FILTER (WHERE cp = 'P') AS put_volume,
           sum(oi_change) FILTER (WHERE cp = 'C') AS call_oi_change,
           sum(oi_change) FILTER (WHERE cp = 'P') AS put_oi_change,
           sum(gxoi) FILTER (WHERE cp = 'C') AS call_gxoi,
           sum(gxoi) FILTER (WHERE cp = 'P') AS put_gxoi,
           sum(dxoi) AS net_dxoi,
           sum(vxoi) AS net_vxoi,
           count(*) AS n_contracts
    FROM base GROUP BY symbol, d
),
by_strike AS (
    SELECT symbol, d, cp, strike, sum(gxoi) AS g
    FROM base WHERE dte BETWEEN 0 AND 60 AND gxoi IS NOT NULL
    GROUP BY symbol, d, cp, strike
),
walls AS (
    SELECT DISTINCT ON (symbol, d, cp) symbol, d, cp, strike, g
    FROM by_strike ORDER BY symbol, d, cp, g DESC, strike
),
atm AS (
    SELECT DISTINCT ON (b.symbol, b.d) b.symbol, b.d,
           avg(b.iv) OVER (PARTITION BY b.symbol, b.d, b.expiry, b.strike) AS iv
    FROM base b JOIN spot s ON s.symbol = b.symbol AND s.d = b.d
    WHERE b.dte BETWEEN 20 AND 45 AND b.iv IS NOT NULL AND b.iv > 0
    ORDER BY b.symbol, b.d, abs(b.strike - s.close), abs(b.dte - 30)
)
INSERT INTO oi_chain_daily (
    symbol, ts, source, spot, call_oi, put_oi, call_volume, put_volume,
    call_oi_change, put_oi_change, net_gxoi, call_gxoi, put_gxoi, net_dxoi, net_vxoi,
    call_wall, call_wall_gxoi, put_wall, put_wall_gxoi, atm_iv_30d, n_contracts)
SELECT a.symbol, a.d, a.source, s.close, a.call_oi, a.put_oi, a.call_volume,
       a.put_volume, a.call_oi_change, a.put_oi_change,
       coalesce(a.call_gxoi, 0) - coalesce(a.put_gxoi, 0), a.call_gxoi, a.put_gxoi,
       a.net_dxoi, a.net_vxoi, wc.strike, wc.g, wp.strike, wp.g, atm.iv, a.n_contracts
FROM agg a
LEFT JOIN spot s ON s.symbol = a.symbol AND s.d = a.d
LEFT JOIN walls wc ON wc.symbol = a.symbol AND wc.d = a.d AND wc.cp = 'C'
LEFT JOIN walls wp ON wp.symbol = a.symbol AND wp.d = a.d AND wp.cp = 'P'
LEFT JOIN atm ON atm.symbol = a.symbol AND atm.d = a.d
ON CONFLICT ON CONSTRAINT uq_oi_chain_daily DO UPDATE SET
    source = EXCLUDED.source, spot = EXCLUDED.spot,
    call_oi = EXCLUDED.call_oi, put_oi = EXCLUDED.put_oi,
    call_volume = EXCLUDED.call_volume, put_volume = EXCLUDED.put_volume,
    call_oi_change = EXCLUDED.call_oi_change, put_oi_change = EXCLUDED.put_oi_change,
    net_gxoi = EXCLUDED.net_gxoi, call_gxoi = EXCLUDED.call_gxoi,
    put_gxoi = EXCLUDED.put_gxoi, net_dxoi = EXCLUDED.net_dxoi,
    net_vxoi = EXCLUDED.net_vxoi, call_wall = EXCLUDED.call_wall,
    call_wall_gxoi = EXCLUDED.call_wall_gxoi, put_wall = EXCLUDED.put_wall,
    put_wall_gxoi = EXCLUDED.put_wall_gxoi, atm_iv_30d = EXCLUDED.atm_iv_30d,
    n_contracts = EXCLUDED.n_contracts
""")


def run(session: Session, *, since: date | None = None) -> int:
    """Aggregate every (symbol, day) in ``oi_chain_eod`` on/after ``since``. Returns rows."""
    since = since or (eastern_now().date() - timedelta(days=DEFAULT_LOOKBACK_DAYS))
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="oi_chain_daily")
    bound.info("oi_chain_daily.start", since=since.isoformat())
    res = session.execute(_SQL, {"since": since, "since_d": since})
    session.commit()
    n = res.rowcount or 0
    bound.info("oi_chain_daily.done", rows=n)
    return n


if __name__ == "__main__":  # pragma: no cover
    s = get_settings()
    with make_session_factory(s)() as sess:
        run(sess, since=date(2000, 1, 1) if "--all" in sys.argv else None)
