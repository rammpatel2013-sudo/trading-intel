"""Scheduled job: capture the market-wide option tape into ``tas_prints`` (Phase 3).

The NAS analogue of ``scripts/tas_capture.py``. One idempotent shot per run:
pull the live, market-wide time-and-sales tape, decode each contract, keep the
prints worth ``min_premium`` notional (price*size*100), and upsert them into
``tas_prints``. Run it every minute during RTH (DSM task / runner cron); the
unique key ``(ts, symbol, price, size, source)`` dedupes overlap between runs
(rule 5).

Index prints (SPX/SPXW/SPY/QQQ) are normally excluded (covered by other jobs),
but the BIG ones (notional >= the index floor) are UN-excluded here and stored
as ``source='convex_index'`` — 0 extra Convex calls (they are already in the
same market-wide response). Each print also carries the observed
``exchange_sale_conditions`` code and derived tags (sweep / block / deep-ITM
financing), plus a same-poll ``leg_group`` clustering size-matched legs of one
structure (a vertical / fly / calendar / roll). ``side`` is the per-leg
aggressor, NOT the trade's direction — never sum it (rule 4).

The tape is live-only — after the 4pm close it returns zeroed trade fields, so
the job self-guards market hours (``intraday_flow.is_market_hours``) and also
skips a poll whose prints are all zero-notional. Rule 1: the only Convex entry
point is the injected ``OptionsDataSource``.

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
_SOURCE_INDEX = "convex_index"
_UQ_COLS = ["ts", "symbol", "price", "size", "source"]
_INSERT_BATCH = 1000
_CONTRACT_RE = re.compile(r"^\.?([A-Za-z]+)(\d{6})([CcPp])(\d+(?:\.\d+)?)$")
_LEG_WINDOW_S = 2.0                       # legs within this window (same root) = one structure
_SWEEP_CODES = frozenset({"I"})          # ISO / intermarket sweep
_BLOCK_CODES = frozenset({"t", "m", "D"})  # negotiated / block prints on the tape
_BLOCK_MIN_SIZE = 250                     # large single print = block even without a block code
_INDEX_ETF = frozenset({"SPY", "QQQ"})   # index ETFs get a lower premium floor than SPX/SPXW


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


def _index_floor(root: str, base: float) -> float:
    """Index premium floor: full for SPX/SPXW, lower for the ETFs (SPY/QQQ)."""
    return base * 0.4 if root in _INDEX_ETF else base


def _is_financing(cp: str | None, delta: float | None, dte: int | None) -> bool:
    """Deep-ITM call (synthetic long / financing) or long-dated high-delta LEAP."""
    if cp != "C" or delta is None or dte is None:
        return False
    ad = abs(delta)
    return ad >= 0.85 or (ad >= 0.70 and dte >= 365)


def _row_record(
    row: pd.Series, *, captured_at: datetime, trade_date: date, min_premium: float,
    exclude: frozenset[str] = frozenset(),
    index_roots: frozenset[str] = frozenset(), index_base_premium: float = 250_000.0,
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

    ru = root.upper() if root else None
    is_index = ru is not None and ru in index_roots
    # Excluded roots are dropped UNLESS they are a big index print (un-excluded here).
    if ru is not None and ru in exclude:
        if not (is_index and notional >= _index_floor(ru, index_base_premium)):
            return None  # small index / other high-volume root covered by other jobs
    source = _SOURCE_INDEX if (is_index and ru in exclude) else _SOURCE

    ts = pd.to_datetime(row.get("time"), errors="coerce")
    ts_dt = ts.to_pydatetime() if pd.notna(ts) else captured_at

    delta = _f(row.get("delta"))
    dte = (expiry_d - trade_date).days if expiry_d is not None else None
    cond = row.get("exchange_sale_conditions")
    cond = str(cond).strip() if pd.notna(cond) else None

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
        "delta": delta,
        "gamma": _f(row.get("gamma")),
        "vega": _f(row.get("vega")),
        "theta": _f(row.get("theta")),
        "iv": _f(row.get("iv") if pd.notna(row.get("iv")) else row.get("volatility")),
        "condition": cond,
        "is_sweep": bool(cond in _SWEEP_CODES),
        "is_block": bool((cond in _BLOCK_CODES) or (size >= _BLOCK_MIN_SIZE)),
        "is_financing": _is_financing(cp, delta, dte),
        "leg_group": None,  # filled by _assign_leg_groups over the whole poll
        "source": source,
    }


def _assign_leg_groups(recs: list[dict], window_s: float = _LEG_WINDOW_S) -> None:
    """Tag size-matched, same-root, near-simultaneous legs with a shared group id.

    A structure (vertical / fly / calendar / roll) prints its legs within the
    same instant; both land in one poll. Singletons keep ``leg_group=None``.
    """
    order = sorted(range(len(recs)), key=lambda i: (recs[i]["root"] or "", recs[i]["ts"]))
    cluster: list[int] = []

    def flush(c: list[int]) -> None:
        if len(c) >= 2:
            gid = uuid.uuid4().hex[:24]
            for i in c:
                recs[i]["leg_group"] = gid

    for i in order:
        r = recs[i]
        if cluster and r["root"] == recs[cluster[-1]]["root"] \
                and abs((r["ts"] - recs[cluster[-1]]["ts"]).total_seconds()) <= window_s:
            cluster.append(i)
        else:
            flush(cluster)
            cluster = [i]
    flush(cluster)


def _records(
    df: pd.DataFrame, *, captured_at: datetime, trade_date: date, min_premium: float,
    exclude: frozenset[str] = frozenset(),
    index_roots: frozenset[str] = frozenset(), index_base_premium: float = 250_000.0,
) -> list[dict]:
    out: list[dict] = []
    for _, row in df.iterrows():
        rec = _row_record(
            row, captured_at=captured_at, trade_date=trade_date, min_premium=min_premium,
            exclude=exclude, index_roots=index_roots, index_base_premium=index_base_premium,
        )
        if rec is not None:
            out.append(rec)
    _assign_leg_groups(out)
    return out


def run(
    session: Session,
    source: OptionsDataSource,
    *,
    settings: Settings | None = None,
    min_premium: float | None = None,
    limit: int | None = None,
    force: bool = False,
) -> None:
    """Capture one poll of the market-wide tape into ``tas_prints`` (idempotent).

    ``min_premium`` / ``limit`` default to ``Settings.TAS_MIN_PREMIUM`` /
    ``Settings.TAS_LIMIT`` (both overridable via ``.env``) when not passed. Big
    index prints above ``Settings.TAS_INDEX_MIN_PREMIUM`` are un-excluded and
    stored as ``source='convex_index'``.
    """
    settings = settings or get_settings()
    min_premium = settings.TAS_MIN_PREMIUM if min_premium is None else min_premium
    limit = settings.TAS_LIMIT if limit is None else limit
    exclude = frozenset(
        r.strip().upper() for r in str(settings.TAS_EXCLUDE_ROOTS).split(",") if r.strip()
    )
    index_roots = frozenset(settings.index_roots) | {"SPXW"}
    index_base = float(getattr(settings, "TAS_INDEX_MIN_PREMIUM", 250_000.0))
    correlation_id = uuid.uuid4().hex
    bound = log.bind(correlation_id=correlation_id, job="tas_capture")

    now = eastern_now()
    if not force and not is_market_hours(now):
        bound.info("tas_capture.skipped_off_hours", now=now.isoformat())
        return

    try:
        # orderby="time" = the chronological tape (every $25k+ print as it prints);
        # the default "value" would only ever return the premium leaderboard.
        df = source.time_and_sales(None, limit=limit, orderby="time")
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
        df, captured_at=captured_at, trade_date=now.date(), min_premium=min_premium,
        exclude=exclude, index_roots=index_roots, index_base_premium=index_base,
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
    idx_kept = sum(1 for r in records if r["source"] == _SOURCE_INDEX)
    bound.info("tas_capture.done", polled=int(len(df)), kept=len(records), index_kept=idx_kept)


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
