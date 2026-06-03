"""Scheduled job: capture the market-wide option tape into ``tas_prints`` (Phase 3).

The NAS analogue of ``scripts/tas_capture.py``. One idempotent shot per run:
pull the live, market-wide time-and-sales tape, decode each contract, keep the
prints worth ``min_premium`` notional (price*size*100), and upsert them into
``tas_prints``. Run it every minute during RTH (DSM task / runner cron); the
unique key ``(ts, symbol, price, size, source)`` dedupes overlap between runs
(rule 5).

The tape is live-only — after the 4pm close it returns zeroed trade fields, so
the job self-guards market hours (``intraday_flow.is_market_hours``) and also
skips a poll whose prints are all zero-notional. Rule 1: the only Convex entry
point is the injected ``OptionsDataSource``. Rule 4: descriptive capture, no
signals.

Manual run (ignores the market-hours guard):
    python -m trading_intel.scheduler.jobs.tas_capture_job
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import pandas as pd
import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.clients import OptionsDataSource
from trading_intel.config import Settings, get_settings
from trading_intel.errors import TradingIntelError
from trading_intel.greeks.intraday_flow import is_market_hours
from trading_intel.memory.models import TasPrint
from trading_intel.timeutils import eastern_now

log = structlog.get_logger(__name__)

_SOURCE = "convex"
_UQ_COLS = ["ts", "symbol", "price", "size", "source"]
_INSERT_BATCH = 1000
_DEFAULT_MIN_PREMIUM = 25_000.0
_DEFAULT_LIMIT = 500
_CONTRACT_RE = re.compile(r"^\.?([A-Za-z]+)(\d{6})([CcPp])(\d+(?:\.\d+)?)$")


def _f(value: object) -> float | None:
    num = pd.to_numeric(value, errors="coerce")
    return float(num) if pd.notna(num) else None


def _i(value: object) -> int | None:
    num = pd.to_numeric(value, errors="coerce")
    return int(num) if pd.notna(num) else None


def _decode(symbol: str) -> tuple[str | None, date | None, str | None, float | None]:
    """Decode a Convex option symbol (``.NVDA260619C230`` -> NVDA / date / C / 230)."""
    m = _CONTRACT_RE.match(str(symbol).strip())
    if not m:
        return None, None, None, None
    root, ymd, cp, strike = m.groups()
    try:
        expiry = datetime.strptime(ymd, "%y%m%d").date()
    except ValueError:
        expiry = None
    return root.upper(), expiry, cp.upper(), float(strike)


def _norm_side(value: object) -> str:
    side = str(value).strip().lower()
    return side if side in {"buy", "sell", "mid"} else "unknown"


def _norm_cp(value: object) -> str | None:
    c = str(value).strip().upper()[:1]
    return c if c in {"C", "P"} else None


def _row_record(
    row: pd.Series, *, captured_at: datetime, trade_date: date, min_premium: float
) -> dict | None:
    """Build one ``tas_prints`` row from a tape row, or None if it's dropped."""
    price = _f(row.get("price"))
    size = _i(row.get("size"))
    if price is None or size is None or size <= 0:
        return None
    notional = price * size * 100.0
    if notional < min_premium:
        return None

    # Prefer the vendor's decoded columns; fall back to parsing the raw symbol.
    # (Optional columns arrive as NaN, not None, when absent for a row.)
    raw = row.get("symbol")
    raw = str(raw) if pd.notna(raw) else None
    root = row.get("root")
    root = str(root) if pd.notna(root) else None
    cp = _norm_cp(row.get("cp") if pd.notna(row.get("cp")) else row.get("opt_kind"))
    strike = _f(row.get("strike"))
    expiry = row.get("expiry") if pd.notna(row.get("expiry")) else row.get("expiration")
    expiry = pd.to_datetime(expiry, errors="coerce")
    expiry_d = expiry.date() if pd.notna(expiry) else None
    if (root is None or strike is None or cp is None or expiry_d is None) and raw is not None:
        d_root, d_exp, d_cp, d_strike = _decode(raw)
        root = root or d_root
        cp = cp or d_cp
        strike = strike if strike is not None else d_strike
        expiry_d = expiry_d or d_exp
    if raw is None and root is not None and expiry_d is not None and cp is not None:
        raw = f".{root}{expiry_d:%y%m%d}{cp}{strike:g}"
    if raw is None:
        return None

    ts = pd.to_datetime(row.get("time"), errors="coerce")
    ts_dt = ts.to_pydatetime() if pd.notna(ts) else captured_at

    return {
        "captured_at": captured_at,
        "ts": ts_dt,
        "trade_date": trade_date,
        "symbol": str(raw),
        "root": root,
        "expiry": expiry_d,
        "strike": strike,
        "cp": cp,
        "side": _norm_side(
            row.get("side") if pd.notna(row.get("side")) else row.get("aggressor_side")
        ),
        "price": price,
        "size": size,
        "notional": round(notional, 2),
        "spot": _f(row.get("spot")),
        "delta": _f(row.get("delta")),
        "gamma": _f(row.get("gamma")),
        "vega": _f(row.get("vega")),
        "theta": _f(row.get("theta")),
        "iv": _f(row.get("iv") if pd.notna(row.get("iv")) else row.get("volatility")),
        "source": _SOURCE,
    }


def _records(
    df: pd.DataFrame, *, captured_at: datetime, trade_date: date, min_premium: float
) -> list[dict]:
    out: list[dict] = []
    for _, row in df.iterrows():
        rec = _row_record(
            row, captured_at=captured_at, trade_date=trade_date, min_premium=min_premium
        )
        if rec is not None:
            out.append(rec)
    return out


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    min_premium: float = _DEFAULT_MIN_PREMIUM,
    limit: int = _DEFAULT_LIMIT,
    force: bool = False,
) -> None:
    """Capture one poll of the market-wide tape into ``tas_prints`` (idempotent)."""
    settings = settings or get_settings()
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="tas_capture")

    now = eastern_now()
    if not force and not is_market_hours(now):
        bound.info("tas_capture.skipped_off_hours", now=now.isoformat())
        return

    try:
        df = source.time_and_sales(None, limit=limit)
    except TradingIntelError as exc:
        bound.warning("tas_capture.fetch_failed", error=str(exc))
        return
    if df is None or df.empty:
        bound.warning("tas_capture.empty_tape")
        return

    # After-hours / pre-open the tape returns zeroed trade fields; don't store noise.
    prem = pd.to_numeric(df.get("price"), errors="coerce") * pd.to_numeric(
        df.get("size"), errors="coerce"
    )
    if prem is None or float(prem.fillna(0).abs().sum()) <= 0:
        bound.warning("tas_capture.zeroed_tape", rows=int(len(df)))
        return

    captured_at = now.replace(microsecond=0)
    records = _records(
        df, captured_at=captured_at, trade_date=now.date(), min_premium=min_premium
    )
    if not records:
        bound.info("tas_capture.no_qualifying_prints", polled=int(len(df)))
        return

    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    _insert = sqlite_insert if dialect == "sqlite" else pg_insert
    for start in range(0, len(records), _INSERT_BATCH):
        batch = records[start : start + _INSERT_BATCH]
        stmt = _insert(TasPrint).values(batch).on_conflict_do_nothing(index_elements=_UQ_COLS)
        session.execute(stmt)
    session.commit()
    bound.info("tas_capture.done", polled=int(len(df)), kept=len(records))


def main() -> None:
    """Manual entrypoint: wire Settings -> session -> ConvexClient, run once (forced)."""
    from trading_intel.clients.convex import ConvexClient
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
    source = ConvexClient(settings)
    session_factory = make_session_factory(settings)
    with session_factory() as session:
        run(session, source, settings=settings, force=True)


if __name__ == "__main__":
    main()
