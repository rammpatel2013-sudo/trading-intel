"""contracts_sync - created 2026-11-19 (DB wave A2 step 6). Fill ``contracts`` from the nightly optionsdata.ai files.

Reads reports/optionsdata/<YYYYMMDD>/<SYM>.csv.gz (scripts/od_nightly.py, 16:20) - the record="contract" rows carry
every spelling of one option: OCC 21-char key (``symbol``), optionsdata iid, Polygon ticker (= CVForge) and dxfeed
symbol (= ConvexValue), plus root, settlement am/pm, exercise, multiplier, first_seen. No vendor calls.
Upsert on the OCC key: first_seen = the earliest seen, last_seen = the latest file day. Also stores the underlying's
optionsdata iid in symbol_map.od_iid. Idempotent.
Run: python -m trading_intel.scheduler.jobs.contracts_sync [--day YYYYMMDD | --all] [--dir reports/optionsdata]
DSM "TI contracts" Mon-Fri 17:20: run_job.sh contracts_sync symbol_map_sync
"""

from __future__ import annotations

import argparse
import csv
import gzip
import sys
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

import structlog
from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from trading_intel.clients.symbols import map_row, normalize_symbol, parse_occ
from trading_intel.config import get_settings
from trading_intel.memory.models_ref import Contract, SymbolMap
from trading_intel.timeutils import eastern_now

log = structlog.get_logger()
ROOT = Path(__file__).resolve().parents[3]
OD_DIR = ROOT / "reports" / "optionsdata"
csv.field_size_limit(10_000_000)


def _f(x: str | None) -> float | None:
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def _d(x: str | None) -> date | None:
    try:
        return date.fromisoformat(x[:10]) if x else None
    except ValueError:
        return None


def contract_rows(
    lines: Iterable[dict], underlying: str, day: date
) -> tuple[list[dict], int | None]:
    """csv dict rows -> (contracts rows, underlying iid). Only record == "contract" with a valid OCC key."""
    out: dict[str, dict] = {}
    und_iid = None
    for r in lines:
        if r.get("record") != "contract":
            continue
        occ = (r.get("symbol") or "").strip()
        p = parse_occ(occ)
        if not p:
            continue
        root, exp, cp, k = p
        occ = f"{root:<6}{occ.replace(' ', '')[len(root):]}"
        iid = _f(r.get("iid"))
        ui = _f(r.get("underlying_iid"))
        if ui and und_iid is None:
            und_iid = int(ui)
        st = (r.get("settlement") or "").lower()[:2] or None
        out[occ] = {
            "occ": occ,
            "underlying": underlying,
            "root": root,
            "expiry": date.fromisoformat(exp),
            "cp": cp,
            "strike": k,
            "settlement": st,
            "exercise": (r.get("exercise") or None),
            "multiplier": _f(r.get("multiplier")),
            "od_iid": int(iid) if iid else None,
            "polygon": r.get("polygon") or None,
            "dxfeed": r.get("dxfeed") or None,
            "first_seen": min(x for x in (_d(r.get("first_seen")), day) if x),
            "last_seen": day,
        }
    return list(out.values()), und_iid


def save(session: Session, rows: list[dict], chunk: int = 2000) -> int:
    if not rows:
        return 0
    now = eastern_now().replace(microsecond=0)
    n = 0
    for i in range(0, len(rows), chunk):
        part = [dict(r, ingested_at=now) for r in rows[i : i + chunk]]
        ins = pg_insert(Contract).values(part)
        ex = ins.excluded
        stmt = ins.on_conflict_do_update(
            index_elements=["occ"],
            set_={
                **{
                    c: getattr(ex, c)
                    for c in (
                        "underlying",
                        "root",
                        "expiry",
                        "cp",
                        "strike",
                        "settlement",
                        "exercise",
                        "multiplier",
                        "od_iid",
                        "polygon",
                        "dxfeed",
                        "ingested_at",
                    )
                },
                "first_seen": func.least(Contract.first_seen, ex.first_seen),
                "last_seen": func.greatest(Contract.last_seen, ex.last_seen),
            },
        )
        session.execute(stmt)
        n += len(part)
    session.commit()
    return n


def set_und_iid(session: Session, symbol: str, iid: int) -> None:
    if session.get(SymbolMap, symbol) is None:
        from trading_intel.clients.symbols import asset_type_of

        session.add(SymbolMap(**map_row(symbol, asset_type_of(symbol)), od_iid=iid))
    else:
        session.execute(update(SymbolMap).where(SymbolMap.symbol == symbol).values(od_iid=iid))
    session.commit()


def files(base: Path, day: str | None, all_days: bool) -> Iterator[tuple[date, str, Path]]:
    days = sorted(
        p for p in base.glob("2*") if p.is_dir() and p.name.isdigit() and len(p.name) == 8
    )
    if day:
        days = [p for p in days if p.name == day]
    elif not all_days:
        days = days[-1:]
    for d in days:
        dd = date(int(d.name[:4]), int(d.name[4:6]), int(d.name[6:]))
        for f in sorted(d.glob("*.csv.gz")):
            if f.name.endswith("_underlying.csv.gz") or f.name.startswith("api_calls"):
                continue
            yield dd, normalize_symbol(f.name[: -len(".csv.gz")]), f


def run(
    session: Session, base: Path = OD_DIR, day: str | None = None, all_days: bool = False
) -> int:
    n = 0
    seen = 0
    for dd, sym, f in files(base, day, all_days):
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            rows, ui = contract_rows(csv.DictReader(fh), sym, dd)
        n += save(session, rows)
        if ui:
            set_und_iid(session, sym, ui)
        seen += 1
        log.info("contracts_sync.file", day=str(dd), symbol=sym, rows=len(rows))
    if not seen:
        raise RuntimeError(f"contracts_sync: no optionsdata files under {base} (day={day})")
    log.info("contracts_sync.done", files=seen, rows=n)
    try:
        from trading_intel.scheduler import track

        track.report(rows_attempted=n, rows_written=n)
    except Exception:  # noqa: S110 - bookkeeping only
        pass
    return n


def main(argv: list[str] | None = None) -> int:
    from trading_intel.memory.db import make_session_factory

    ap = argparse.ArgumentParser(prog="contracts_sync")
    ap.add_argument("--day")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dir", default=str(OD_DIR))
    a = ap.parse_args(argv)
    with make_session_factory(get_settings())() as session:
        print(run(session, Path(a.dir), a.day, a.all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
