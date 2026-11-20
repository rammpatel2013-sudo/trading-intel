"""Chain bank job (2026-11-20) - every option + 1-minute price/volume of ~1,550 names, hourly, into Postgres.

NAS DSM task, Mon-Fri (user root):
    "TI chain bank"   10:05, repeat every 60 min until 16:05   run_job.sh chain_bank
(the 16:05 run is the day's closing book.) Universe: current S&P 1500 (equity_members) + QQQ holdings
(FMP etf/holdings) + the ETF board list + MACRO_ETFS + SPX NDX RUT VIX XSP.

Each run (one timestamp ``ts`` = start minute, naive Eastern, via eastern_now):
  options  CVForge options_snapshots (refreshes ~every 3 min; IV, greeks, OI, day volume / close / VWAP;
           no bid/ask) -> greeks_chain, source cvforge_bank, every contract (~1.4M rows), written page by
           page as it arrives (low RAM). vanna / charm / gxoi / dxoi / vxoi synthesized exactly as
           CVForgeClient.chain() does, so readers can use the rows like any other chain.
  totals   one greeks_snapshots row per name (source cvforge_bank): gex/dex/vex/chex totals, gex flip, ATM
           IV, call/put volume + delta notional (the house maths, same as the sector rows) + IV30 / IV90 /
           term / 25-delta skew / call-put OI / call-put $ premium.
  bars     FMP 1-minute bars via CVForge, today, every name -> bars_1m (source 'fmp', upsert: the same rows
           flow_bank writes, so one copy).
Hour-on-hour flow and the option-$ VWAP are read from consecutive greeks_snapshots rows (derived, not stored).
Pace: at most PACE_PER_MIN CVForge calls a minute for this job (the key allows ~830; other jobs share it).
Descriptors only (rule 4); no AI (rule 7).

    python -m trading_intel.scheduler.jobs.chain_bank [--steps options,bars] [--force] [--limit N]
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel import chain_bank as cb
from trading_intel.config import Settings, get_settings
from trading_intel.timeutils import eastern_now, is_trading_session, live_expiry_floor

log = structlog.get_logger(__name__)

PACE_PER_MIN = 400
WORKERS = 4
STEPS = ("options", "bars")
INSERT_BATCH = 5_000


class Pacer:
    """At most ``per_min`` calls a minute across threads."""

    def __init__(self, per_min: int, clock=time.monotonic, sleep=time.sleep) -> None:  # noqa: ANN001
        self.gap = 60.0 / max(1, per_min)
        self.next = 0.0
        self.lock = threading.Lock()
        self.clock, self.sleep = clock, sleep

    def wait(self) -> None:
        with self.lock:
            now = self.clock()
            t = max(now, self.next)
            self.next = t + self.gap
        if t > now:
            self.sleep(t - now)


def _query(cv, sql: str) -> list[dict]:  # noqa: ANN001
    data = cv._post("/query", {"sql": sql, "max_rows": cb.PAGE})  # noqa: SLF001 - raw rows, no DataFrame round trip
    return data.get("rows", []) or []


def _retry(fn, tries: int = 3, sleep=time.sleep):  # noqa: ANN001, ANN202
    err = None
    for a in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            err = exc
            sleep(2.0 * (a + 1))
    raise err  # type: ignore[misc]


def _copy_insert(session: Session, table: str, rows: list[dict], keys: list[str]) -> int:
    """Postgres fast path (~20x the multi-row INSERT): COPY into a temp table, then INSERT ... SELECT
    ON CONFLICT (natural key) DO NOTHING - same idempotent result, one round trip per batch."""
    cols = list(rows[0])
    conn = session.connection().connection.driver_connection  # psycopg 3 connection inside this transaction
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE IF NOT EXISTS _cb_{table} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS")
        cur.execute(f"TRUNCATE _cb_{table}")
        with cur.copy(f"COPY _cb_{table} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r[c] for c in cols])
        cur.execute(f"INSERT INTO {table} ({', '.join(cols)}) SELECT {', '.join(cols)} FROM _cb_{table} "  # noqa: S608
                    f"ON CONFLICT ({', '.join(keys)}) DO NOTHING")
    return len(rows)


def _insert(session: Session, model, rows: list[dict], keys: list[str]) -> int:  # noqa: ANN001
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql" and len(rows) > 500:
        return _copy_insert(session, model.__tablename__, rows, keys)
    # one statement may carry at most 65,535 parameters in Postgres (32,766 in SQLite)
    per = max(1, min(INSERT_BATCH, (60_000 if dialect == "postgresql" else 30_000) // max(1, len(rows[0]))))
    for i in range(0, len(rows), per):
        part = rows[i:i + per]
        if dialect == "postgresql":
            session.execute(pg_insert(model).values(part).on_conflict_do_nothing(index_elements=keys))
        else:  # SQLite tests
            from sqlalchemy.dialects.sqlite import insert as sl_insert

            session.execute(sl_insert(model).values(part).on_conflict_do_nothing(index_elements=keys))
    return len(rows)


def load_universe(session: Session, cv, day: date) -> list[str]:  # noqa: ANN001
    from trading_intel.etf_board import ETF_LIST
    from trading_intel.memory.models_equity import EquityMember

    rows = session.execute(
        select(EquityMember.symbol).where((EquityMember.end_date.is_(None)) | (EquityMember.end_date >= day))
    ).scalars().all()
    qqq: list[str] = []
    try:
        raw = cv.fmp("etf/holdings", {"symbol": "QQQ"})
        qqq = [str(r.get("asset") or "") for r in (raw or []) if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 - the S&P 1500 already holds almost all of them
        log.warning("chain_bank.qqq_holdings_failed", error=str(exc)[:120])
    return cb.universe(rows, qqq, ETF_LIST)


def _cv_name(sym: str) -> str:
    from trading_intel.clients.symbols import vendor_symbol

    return vendor_symbol(sym, "cvforge", "index" if sym in cb.INDEXES else None) or sym


def pull_options(session: Session, cv, names: list[str], day: date, ts: datetime, stamp: datetime,  # noqa: ANN001
                 pacer: Pacer, *, now: datetime, workers: int = WORKERS) -> dict:
    """Whole chains for ``names`` -> greeks_chain (page by page) + one greeks_snapshots row per name."""
    from trading_intel.memory.models import GreeksChain, GreeksSnapshot

    ours = {_cv_name(s): s for s in names}
    keys = list(ours)
    counts: dict[str, int] = {}
    for i in range(0, len(keys), 400):  # the IN list stays short
        pacer.wait()
        rows = _retry(lambda k=keys[i:i + 400]: _query(
            cv, f"select underlying_ticker u, count(*) n from options_snapshots where underlying_ticker in ({cb.sql_list(k)}) group by 1"))  # noqa: S608
        counts.update({r["u"]: int(r["n"]) for r in rows})
    groups = cb.batches(counts)
    cols = ", ".join(cb.SNAP_COLS)
    floor = live_expiry_floor(day, now)
    lock = threading.Lock()
    st = {"underlyings": len(counts), "missing": sorted(set(keys) - set(counts))[:50], "calls": 0, "rows": 0,
          "chain_rows": 0}

    def one(group: list[str]) -> list[pd.DataFrame]:
        got, off = [], 0
        while True:
            pacer.wait()
            sql = (f"select {cols} from options_snapshots where underlying_ticker in ({cb.sql_list(group)}) "  # noqa: S608
                   f"order by ticker limit {cb.PAGE} offset {off}")
            rows = _retry(lambda s=sql: _query(cv, s))
            with lock:
                st["calls"] += 1
                st["rows"] += len(rows)
            if rows:
                got.append(cb.add_greeks(cb.tidy(pd.DataFrame(rows, columns=list(cb.SNAP_COLS)), ours), day, floor))
            if len(rows) < cb.PAGE:
                return got
            off += cb.PAGE

    # fetch in threads; write in this thread (one DB session) as each group finishes. A group always holds
    # whole underlyings (big chains are paged inside one group), so its name totals are computed right away
    # and the frame is dropped - memory stays at one group (~10k-30k rows).
    snaps: list[dict] = []
    with ThreadPoolExecutor(workers) as ex:
        for got in ex.map(one, groups):
            d = pd.concat([x for x in got if len(x)], ignore_index=True) if any(len(x) for x in got) else None
            if d is None:
                continue
            st["chain_rows"] += _insert(session, GreeksChain, cb.chain_records(d, ts, stamp),
                                        ["symbol", "ts", "source", "root", "expiry", "strike", "cp"])
            session.commit()
            for sym, g in d.groupby("symbol", sort=True):
                try:
                    r = cb.snapshot_record(g, sym, day, ts, stamp)
                except Exception as exc:  # noqa: BLE001 - one bad chain never stops the run
                    log.warning("chain_bank.snapshot_failed", symbol=sym, error=str(exc)[:120])
                    continue
                if r:
                    snaps.append(r)
    st["snapshots"] = _insert(session, GreeksSnapshot, snaps, ["symbol", "ts", "source"])
    session.commit()
    st["groups"] = len(groups)
    return st


def pull_bars(session: Session, cv, names: list[str], day: date, pacer: Pacer, *, workers: int = WORKERS) -> dict:  # noqa: ANN001
    from trading_intel.clients.symbols import vendor_symbol
    from trading_intel.flow import bank

    bad: list[str] = []
    lock = threading.Lock()

    def one(sym: str) -> list[dict]:
        fs = vendor_symbol(sym, "fmp", "index" if sym in cb.INDEXES else None) or sym
        pacer.wait()
        try:
            raw = _retry(lambda: cv.fmp("historical-chart/1min", {"symbol": fs, "from": day.isoformat(), "to": day.isoformat()}), tries=2)
        except Exception:  # noqa: BLE001
            with lock:
                bad.append(sym)
            return []
        return [r for r in bank.parse_fmp_1m(sym, raw) if r["ts"].date() == day]

    n = 0
    names_with = 0
    with ThreadPoolExecutor(workers) as ex:
        for rows in ex.map(one, names):
            if rows:
                n += bank.save_bars_1m(session, rows)
                names_with += 1
    session.commit()
    return {"names": len(names), "with_bars": names_with, "bars": n, "failed": len(bad), "failed_some": bad[:20]}


def run(
    session: Session,
    settings: Settings | None = None,
    *,
    cvforge: object | None = None,
    now: datetime | None = None,
    force: bool = False,
    steps: tuple[str, ...] = STEPS,
    limit: int | None = None,
    pacer: Pacer | None = None,
) -> dict:
    settings = settings or get_settings()
    now = now or eastern_now()
    day = now.date()
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="chain_bank")
    if not force and not is_trading_session(day):
        bound.info("chain_bank.skipped_non_session", day=day.isoformat())
        return {"skipped": "not_a_trading_session"}
    if cvforge is None:
        from trading_intel.clients.cvforge import CVForgeClient

        cvforge = CVForgeClient(settings, timeout=90.0)
    pacer = pacer or Pacer(PACE_PER_MIN)
    names = load_universe(session, cvforge, day)
    if limit:
        names = names[:limit]
    ts = now.replace(second=0, microsecond=0, tzinfo=None)
    out: dict = {"day": day.isoformat(), "ts": ts.isoformat(), "universe": len(names)}
    errors: list[str] = []
    t0 = time.monotonic()
    if "options" in steps:
        try:
            out["options"] = pull_options(session, cvforge, names, day, ts, eastern_now().replace(tzinfo=None),
                                          pacer, now=now)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append(f"options: {type(exc).__name__}: {str(exc)[:200]}")
    if "bars" in steps:
        try:
            out["bars"] = pull_bars(session, cvforge, names, day, pacer)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append(f"bars: {type(exc).__name__}: {str(exc)[:200]}")
    out["secs"] = round(time.monotonic() - t0)
    out["errors"] = errors
    try:
        from trading_intel.scheduler import track

        o = out.get("options") or {}
        track.report(rows_attempted=o.get("rows", 0), rows_written=o.get("chain_rows", 0))
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    (bound.info if not errors else bound.error)("chain_bank.done", **{k: v for k, v in out.items() if k not in ("options", "bars")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="chain_bank")
    ap.add_argument("--steps", default=",".join(STEPS))
    ap.add_argument("--force", action="store_true", help="run on a non-session day")
    ap.add_argument("--limit", type=int, help="first N names only (test)")
    a = ap.parse_args(argv)
    from trading_intel.memory.db import make_session_factory

    st = get_settings()
    with make_session_factory(st)() as s:
        res = run(s, st, force=a.force, steps=tuple(x for x in a.steps.split(",") if x), limit=a.limit)
    print(res)
    return 1 if res.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
