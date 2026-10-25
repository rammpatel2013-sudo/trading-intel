"""One-time history backfill for the fear-release signal + Vol Read (R1e, 2026-10-25).

What it fills
-------------
1. ``vix_data``: INSERTS the sessions that don't exist yet (default since
   2011-01-03) with ``vix``, ``vix9d``, ``vix3m``, ``vix6m``, ``vvix`` from
   Cboe's public daily-history CSVs, and fills those columns where an existing
   row has them NULL. Other columns (MOVE, OAS, VRP…) stay NULL on backfilled
   rows — readers that need them already filter by recency.
2. ``index_skew_daily``: fills NULL ``vixeq`` / ``dspx`` / ``cor1m`` / ``cor3m``
   on existing rows from the same Cboe CSVs (the "VIXEQ collector" gap: the
   Yahoo path returned nothing; the live job now reads Cboe).
3. ``quotes_daily`` symbol ``SPX``: INSERTS missing daily closes from the
   CVForge FMP passthrough (``^GSPC``) since the same start date, so the
   VIX9D/VIX release signal can score forward SPX returns on one table.

Idempotent: inserts use ON CONFLICT DO NOTHING; fills touch NULL cells only.
Never overwrites a non-NULL value. Descriptive data only (rule 4).

Run on the machine with NAS DB access (the laptop), from the project root:
    python scripts/backfill_vol_history.py --dry-run
    python scripts/backfill_vol_history.py
    python scripts/backfill_vol_history.py --since 2015-01-01 --skip-spx
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory
from trading_intel.memory.models import IndexSkewDaily, QuoteDaily, Ticker, VixData

log = structlog.get_logger(__name__)

_CSV = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv"
_VIX_FIELDS = {"vix": "VIX", "vix9d": "VIX9D", "vix3m": "VIX3M", "vix6m": "VIX6M", "vvix": "VVIX"}
_SKEW_FIELDS = {"vixeq": "VIXEQ", "dspx": "DSPX", "cor1m": "COR1M", "cor3m": "COR3M"}


def _fetch_history(name: str) -> dict[date, float]:
    """Parse one Cboe ``{name}_History.csv`` into ``{date: close}`` (CLOSE = last field)."""
    resp = httpx.get(_CSV.format(name=name), timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    out: dict[date, float] = {}
    for line in resp.text.splitlines()[1:]:
        parts = [p.strip() for p in line.split(",") if p.strip() != ""]
        if len(parts) < 2:
            continue
        try:
            d = datetime.strptime(parts[0], "%m/%d/%Y").date()
            out[d] = float(parts[-1])
        except (ValueError, IndexError):
            continue
    return out


def _fetch_spx_closes(since: date) -> dict[date, dict]:
    """``^GSPC`` daily OHLC from the CVForge FMP passthrough, in ~5-year chunks."""
    from trading_intel.clients.cvforge import CvForgeClient

    client = CvForgeClient(get_settings())
    out: dict[date, dict] = {}
    today = date.today()
    start = since
    while start <= today:
        end = min(date.fromordinal(start.toordinal() + 5 * 365), today)
        data = client.fmp(
            "historical-price-eod/full",
            {"symbol": "^GSPC", "from": start.isoformat(), "to": end.isoformat()},
        )
        for r in data if isinstance(data, list) else []:
            try:
                d = datetime.strptime(str(r.get("date"))[:10], "%Y-%m-%d").date()
                close = float(r["close"])
                out[d] = {
                    "open": float(r.get("open") or close),
                    "high": float(r.get("high") or close),
                    "low": float(r.get("low") or close),
                    "close": close,
                    "volume": int(r.get("volume") or 0),
                }
            except (TypeError, ValueError, KeyError):
                continue
        if end >= today:
            break
        start = date.fromordinal(end.toordinal() + 1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default="2011-01-03", help="first session to insert (ISO)")
    ap.add_argument("--skip-spx", action="store_true", help="skip the FMP ^GSPC leg")
    args = ap.parse_args()
    since = datetime.strptime(args.since, "%Y-%m-%d").date()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    vix_hist = {c: _fetch_history(n) for c, n in _VIX_FIELDS.items()}
    skew_hist = {c: _fetch_history(n) for c, n in _SKEW_FIELDS.items()}
    for c, h in {**vix_hist, **skew_hist}.items():
        log.info("backfill.csv", field=c, rows=len(h), last=max(h).isoformat() if h else None)

    settings = get_settings()
    with make_session_factory(settings)() as session:
        # 1. vix_data — insert missing sessions, fill NULLs on existing ones.
        existing = {r.date: r for r in session.execute(select(VixData)).scalars()}
        all_dates = sorted({d for h in vix_hist.values() for d in h if d >= since})
        inserts: list[dict] = []
        filled = 0
        for d in all_dates:
            vals = {c: vix_hist[c].get(d) for c in _VIX_FIELDS}
            if vals["vix"] is None:
                continue
            row = existing.get(d)
            if row is None:
                inserts.append({"date": d, **vals})
            else:
                for c, v in vals.items():
                    if v is not None and getattr(row, c) is None:
                        setattr(row, c, v)
                        filled += 1
        log.info("backfill.vix_data", inserts=len(inserts), cells_filled=filled)
        if inserts and not args.dry_run:
            for i in range(0, len(inserts), 500):
                session.execute(
                    pg_insert(VixData).values(inserts[i : i + 500]).on_conflict_do_nothing(
                        index_elements=["date"]
                    )
                )

        # 2. index_skew_daily — fill NULL vixeq/dspx/cor1m/cor3m.
        n_skew = 0
        for row in session.execute(select(IndexSkewDaily)).scalars():
            for c in _SKEW_FIELDS:
                if getattr(row, c) is None:
                    v = skew_hist[c].get(row.date)
                    if v is not None:
                        setattr(row, c, v)
                        n_skew += 1
        log.info("backfill.index_skew_daily", cells_filled=n_skew)

        # 3. quotes_daily SPX from FMP ^GSPC.
        if not args.skip_spx:
            spx = _fetch_spx_closes(since)
            have = {
                d
                for (d,) in session.execute(
                    select(QuoteDaily.date).where(QuoteDaily.symbol == "SPX")
                )
            }
            new_q = [{"symbol": "SPX", "date": d, **v} for d, v in spx.items() if d not in have]
            log.info("backfill.quotes_daily_spx", fetched=len(spx), inserts=len(new_q))
            if new_q and not args.dry_run:
                session.execute(
                    pg_insert(Ticker)
                    .values({"symbol": "SPX", "name": "S&P 500 index", "is_active": True})
                    .on_conflict_do_nothing(index_elements=["symbol"])
                )
                for i in range(0, len(new_q), 500):
                    session.execute(
                        pg_insert(QuoteDaily).values(new_q[i : i + 500]).on_conflict_do_nothing(
                            constraint="uq_quotes_daily"
                        )
                    )

        if args.dry_run:
            session.rollback()
            log.info("backfill.dry_run", note="nothing written")
        else:
            session.commit()
            log.info("backfill.committed")


if __name__ == "__main__":
    main()
