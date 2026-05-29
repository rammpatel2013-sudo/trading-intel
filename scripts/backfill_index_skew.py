"""One-shot backfill of ``index_skew_daily`` from Yahoo history.

Pulls multi-year history for the three publicly-available Nations indices
(``^VOLI``, ``^TDEX``, ``^SDEX``), then for each trading day in chronological
order upserts the values + their trailing-252d percentile.

What this script DOES NOT do:
- Backfill the ``*_proxy`` columns (CallDex / PutDex / RiskDex) — those need
  the full SPX delta surface for that date, which we only have for dates where
  ``oi_chain_eod`` was populated. The daily ``index_skew`` job fills proxies
  forward from today.
- Touch the Cboe SKEW, VVIX, VIX-options, or SPX-RR columns. The upsert
  whitelist is restricted to VOLI/TDEX/SDEX + their pctiles, so any other
  columns you've populated stay intact.

Use the ``--start`` / ``--end`` flags to control the window. Defaults to the
last ~5 years (yfinance period ``5y``).

Run from repo root:

    python scripts/backfill_index_skew.py                  # last 5y, all three
    python scripts/backfill_index_skew.py --period 10y     # full Yahoo history
    python scripts/backfill_index_skew.py --start 2020-01-01 --end 2026-05-28
    python scripts/backfill_index_skew.py --dry-run        # print, don't write

The script is idempotent: re-running upserts the same values without dropping
other columns. After ~40 backfilled rows accrue, the vol-regime classifier
starts emitting real labels instead of ``MIXED``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

# Ensure the repo root is on sys.path when invoked as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_intel.config import get_settings  # noqa: E402
from trading_intel.memory.db import make_session_factory  # noqa: E402
from trading_intel.memory.models import IndexSkewDaily  # noqa: E402
from trading_intel.vol.skew import skew_percentile  # noqa: E402

log = structlog.get_logger(__name__)

#: Columns this backfill touches. Anything else on ``index_skew_daily`` stays
#: untouched on conflict (``cboe_skew``, ``vvix``, ``spx_rr_*``, the proxies…).
_BACKFILL_COLS = (
    "voli",
    "voli_pctile_252d",
    "tdex",
    "tdex_pctile_252d",
    "sdex",
    "sdex_pctile_252d",
)

_PCTILE_WINDOW = 252


def _yf_history(symbol: str, *, period: str, start: str | None, end: str | None) -> pd.Series:
    """Return a ``date → close`` Series for the requested window."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit("yfinance is not installed; pip install yfinance") from exc

    if start or end:
        raw = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False)
    else:
        raw = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)

    if raw is None or raw.empty:
        log.warning("backfill.yf_empty", sym=symbol)
        return pd.Series(dtype=float)

    closes = raw["Close"].dropna().copy()
    if hasattr(closes.index, "tz") and closes.index.tz is not None:
        closes.index = closes.index.tz_localize(None)
    closes.index = closes.index.normalize()
    closes.index = closes.index.date
    closes.name = symbol
    return closes


def _build_panel(*, period: str, start: str | None, end: str | None) -> pd.DataFrame:
    """Inner-join the three Nations closes on date so each row has all three."""
    voli = _yf_history("^VOLI", period=period, start=start, end=end)
    tdex = _yf_history("^TDEX", period=period, start=start, end=end)
    sdex = _yf_history("^SDEX", period=period, start=start, end=end)
    panel = pd.concat(
        {"voli": voli, "tdex": tdex, "sdex": sdex}, axis=1
    ).dropna(how="all")
    panel.index.name = "date"
    return panel.sort_index()


def _upsert_partial(session: Session, record: dict) -> None:
    """Upsert keyed on ``date``, overwriting only the backfill columns."""
    stmt = pg_insert(IndexSkewDaily).values([record])
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={c: stmt.excluded[c] for c in _BACKFILL_COLS if c in record},
    )
    session.execute(stmt)


def backfill(
    session: Session,
    *,
    period: str = "5y",
    start: str | None = None,
    end: str | None = None,
    dry_run: bool = False,
) -> int:
    """Walk the Yahoo panel in chronological order and upsert each row.

    Percentiles are computed against the trailing ``_PCTILE_WINDOW`` values from
    the same backfill batch — same definition the daily job uses, but evaluated
    in-memory so we don't have to re-read the DB per row.
    """
    panel = _build_panel(period=period, start=start, end=end)
    if panel.empty:
        log.warning("backfill.empty")
        return 0

    log.info("backfill.start", n_rows=len(panel), first=str(panel.index[0]), last=str(panel.index[-1]))

    voli_hist: list[float] = []
    tdex_hist: list[float] = []
    sdex_hist: list[float] = []

    n_written = 0
    for row_date, row in panel.iterrows():
        voli = float(row["voli"]) if pd.notna(row["voli"]) else None
        tdex = float(row["tdex"]) if pd.notna(row["tdex"]) else None
        sdex = float(row["sdex"]) if pd.notna(row["sdex"]) else None

        voli_p = skew_percentile(voli_hist[-_PCTILE_WINDOW:], voli) if voli is not None else None
        tdex_p = skew_percentile(tdex_hist[-_PCTILE_WINDOW:], tdex) if tdex is not None else None
        sdex_p = skew_percentile(sdex_hist[-_PCTILE_WINDOW:], sdex) if sdex is not None else None

        record = {
            "date": row_date,
            "voli": voli,
            "voli_pctile_252d": voli_p,
            "tdex": tdex,
            "tdex_pctile_252d": tdex_p,
            "sdex": sdex,
            "sdex_pctile_252d": sdex_p,
        }

        if dry_run:
            log.info("backfill.dry", **{k: (v if not isinstance(v, date) else str(v)) for k, v in record.items()})
        else:
            _upsert_partial(session, record)
            n_written += 1

        if voli is not None:
            voli_hist.append(voli)
        if tdex is not None:
            tdex_hist.append(tdex)
        if sdex is not None:
            sdex_hist.append(sdex)

    if not dry_run:
        session.commit()
    log.info("backfill.done", written=n_written, dry_run=dry_run)
    return n_written


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--period", default="5y", help="yfinance period when --start/--end not given (default: 5y)")
    p.add_argument("--start", default=None, help="ISO start date (YYYY-MM-DD). Overrides --period.")
    p.add_argument("--end", default=None, help="ISO end date (YYYY-MM-DD). Overrides --period.")
    p.add_argument("--dry-run", action="store_true", help="Print rows; do not write.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = get_settings()
    factory = make_session_factory(settings)
    with factory() as session:
        backfill(
            session,
            period=args.period,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
