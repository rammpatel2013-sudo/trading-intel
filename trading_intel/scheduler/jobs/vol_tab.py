"""Nightly Vol EOD tab job (Phase 5, 2026-11-04) -> DB rows + ``voltab.json``.

Runs after the 22:15 replay (DSM 22:50 ET). End-of-day data only — ZERO Convex
calls. Steps (each idempotent, rule 5):

  V1  vix_signal_snapshots  recompute VIX-overshoot rows from ``vix_data`` + SPX
                            closes; every missing date is filled (first run = backfill)
  V3  straddle_snapshots    SPX 0DTE AM straddle from ``reports/replay/history.csv``
                            (fallback: ``map_daily`` ATM IV estimate)
  then writes ``scripts/cvxmap/data/voltab.json`` for the cockpit Vol EOD tab:
  V1 overshoot · V2 VIX3M/VIX/RV21 · V3 straddle % · V4 F-OTM put-IV floor ·
  V5 Color (time-only change in dealer gamma) · B1 beta-reset summary.

SPX closes come from ``quotes_daily``; dates missing there are filled from
yfinance (free, via ``clients.prices``). Descriptor only (rule 4).

Manual run:
    python -m trading_intel.scheduler.jobs.vol_tab
"""

from __future__ import annotations

import csv
import json
import math
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from trading_intel.memory.models import StraddleSnapshot, VixSignalSnapshot
from trading_intel.timeutils import eastern_now, is_trading_session, live_expiry_floor
from trading_intel.vol import vix_overshoot as vo
from trading_intel.vol import vol_tab as vt

log = structlog.get_logger(__name__)

REPO = Path(__file__).resolve().parents[3]
DATA_DIR = REPO / "scripts" / "cvxmap" / "data"
REPLAY_HIST = REPO / "reports" / "replay" / "history.csv"
EVENTS = REPO / "scripts" / "cvxmap" / "events.json"
SYMBOL = "SPX"
ODTE_YEARS = 405.0 / 525600.0  # 09:30 -> 16:15 settle, calendar minutes
KEEP_POINTS = 520


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------- loaders
def load_vix(session: Session) -> list[tuple[date, float | None, float | None, float | None]]:
    rows = session.execute(text("SELECT date, vix, vix3m, vrp FROM vix_data ORDER BY date")).all()
    return [(r[0], _f(r[1]), _f(r[2]), _f(r[3])) for r in rows]


def load_spx(
    session: Session, need: set[date], price_source: Any | None = None
) -> dict[date, float]:
    rows = session.execute(
        text("SELECT date, close FROM quotes_daily WHERE symbol = :s ORDER BY date"), {"s": SYMBOL}
    ).all()
    spx = {r[0]: float(r[1]) for r in rows if r[1]}
    missing = {d for d in need if d not in spx}
    if missing and price_source is not None:
        try:
            df = price_source.daily_history(SYMBOL, period="5y")
            for d, c in zip(df["date"], df["close"]):
                dd = d.date() if hasattr(d, "date") else d
                if dd in missing and c == c:
                    spx[dd] = float(c)
        except Exception as exc:  # noqa: BLE001 — the fill is best-effort
            log.warning("vol_tab.spx_fill_failed", error=str(exc))
    return spx


def load_events(path: Path = EVENTS) -> tuple[set[date], list[dict]]:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("events") or []
    except (OSError, ValueError):
        return set(), []
    fed = set()
    for r in rows:
        try:
            if r.get("kind") == "Fed":
                fed.add(date.fromisoformat(r["d"]))
        except (KeyError, ValueError):
            continue
    return fed, rows


def replay_straddles(path: Path = REPLAY_HIST) -> dict[date, dict]:
    out: dict[date, dict] = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                continue
            st, op = _f(r.get("am_straddle")), _f(r.get("open"))
            if st and op:
                out[d] = {
                    "spot": op,
                    "straddle": st,
                    "iv": _f(r.get("am_iv")),
                    "on_straddle": _f(r.get("on_straddle")),
                }
    return out


def map_iv_straddles(session: Session) -> dict[date, dict]:
    rows = session.execute(
        text("SELECT date, spot, atm_iv FROM map_daily WHERE symbol = :s AND atm_iv IS NOT NULL"),
        {"s": SYMBOL},
    ).all()
    out = {}
    for d, spot, iv in rows:
        spot, iv = _f(spot), _f(iv)
        if not spot or not iv:
            continue
        if iv > 3:  # stored in vol points
            iv /= 100.0
        st = math.sqrt(2.0 / math.pi) * spot * iv * math.sqrt(ODTE_YEARS)
        out[d] = {"spot": spot, "straddle": st, "iv": iv, "on_straddle": None}
    return out


# ---------------------------------------------------------------- writers
def upsert_overshoot(session: Session, rows: list[vo.OvershootRow]) -> int:
    have = {
        r[0]: r
        for r in session.execute(select(VixSignalSnapshot.date, VixSignalSnapshot.residual)).all()
    }
    now = datetime.utcnow()
    n = 0
    for r in rows:
        prev = have.get(r.date)
        if prev is not None and prev[1] is not None and abs(prev[1] - r.residual) < 1e-9:
            continue
        session.merge(
            VixSignalSnapshot(
                date=r.date,
                vix_actual=r.vix_actual,
                vix_prev=r.vix_prev,
                spx_ret_pct=r.spx_ret_pct,
                alpha=r.alpha,
                beta=r.beta,
                vix_model=r.vix_model,
                residual=r.residual,
                flag=bool(r.flag),
                n_fit=r.n_fit,
                created_at=now,
            )
        )
        n += 1
    session.commit()
    return n


def upsert_straddles(session: Session, rows: dict[date, dict], source_of: dict[date, str]) -> int:
    existing = {
        s.date: s
        for s in session.execute(
            select(StraddleSnapshot).where(StraddleSnapshot.symbol == SYMBOL)
        ).scalars()
    }
    now = datetime.utcnow()
    n = 0
    for d, v in rows.items():
        pct = 100.0 * v["straddle"] / v["spot"]
        src = source_of[d]
        cur = existing.get(d)
        if cur is not None:
            if cur.source == "replay" and src != "replay":
                continue  # never overwrite a measured straddle with the estimate
            if (
                cur.source == src
                and cur.straddle is not None
                and abs(cur.straddle - v["straddle"]) < 1e-6
            ):
                continue
            cur.spot, cur.straddle, cur.pct_of_spot = v["spot"], v["straddle"], pct
            cur.iv, cur.on_straddle, cur.source, cur.created_at = (
                v["iv"],
                v["on_straddle"],
                src,
                now,
            )
        else:
            session.add(
                StraddleSnapshot(
                    symbol=SYMBOL,
                    date=d,
                    spot=v["spot"],
                    straddle=v["straddle"],
                    pct_of_spot=pct,
                    iv=v["iv"],
                    on_straddle=v["on_straddle"],
                    source=src,
                    created_at=now,
                )
            )
        n += 1
    session.commit()
    return n


# ---------------------------------------------------------------- panels
def _next_session_gap(d: date) -> int:
    n = 1
    while not is_trading_session(d + timedelta(days=n)) and n < 6:
        n += 1
    return n


def color_panel(session: Session) -> dict:
    ts = session.execute(
        text("SELECT max(ts) FROM oi_chain_eod WHERE symbol = :s"), {"s": SYMBOL}
    ).scalar()
    out: dict = {
        "series": vt.gex_change_series(
            session.execute(
                text("SELECT ts, net_gxoi FROM oi_chain_daily WHERE symbol = :s ORDER BY ts"),
                {"s": SYMBOL},
            ).all()
        )
    }
    if ts is None:
        return out
    as_of = ts.date() if hasattr(ts, "date") else ts
    spot = session.execute(
        text(
            "SELECT spot FROM oi_chain_daily WHERE symbol = :s AND spot IS NOT NULL ORDER BY ts DESC LIMIT 1"
        ),
        {"s": SYMBOL},
    ).scalar()
    if not spot:
        return out
    chain = session.execute(
        text(
            "SELECT expiry, strike, cp, oi, iv FROM oi_chain_eod "
            "WHERE symbol = :s AND ts = :t AND expiry >= :d AND oi > 0 AND iv > 0"
        ),
        {"s": SYMBOL, "t": ts, "d": live_expiry_floor(as_of)},
    ).all()
    out["now"] = vt.color_from_chain(
        [(r[0], float(r[1]), r[2], r[3], _f(r[4])) for r in chain],
        float(spot),
        as_of,
        step_days=_next_session_gap(as_of),
    )
    return out


def fotm_panel() -> dict:
    """V4: reuse the F-OTM put-IV tracker's own maths (scripts/fotm_put_iv_report.py)."""
    try:
        from trading_intel.reports import _FOTM_PUT_IV_SCRIPT, _load

        mod = _load(_FOTM_PUT_IV_SCRIPT, "_fotm_voltab")
        rows = mod.F.splice(mod._chart_history(), mod._fetch(800))
        v = mod.F.build_view(rows)
    except Exception as exc:  # noqa: BLE001 — one panel must not kill the tab
        log.warning("vol_tab.fotm_failed", error=str(exc))
        return {"err": str(exc)[:200]}
    pts = [
        {
            "d": p.ts.isoformat(),
            "iv": round(p.iv, 4),
            "spot": round(p.spot, 2),
            "st": p.state,
            "src": p.source,
        }
        for p in v["points"]
    ][-KEEP_POINTS:]
    read = {k: (val.isoformat() if isinstance(val, date) else val) for k, val in v["read"].items()}
    return {"points": pts, "read": read, "params": v["params"]}


def beta_panel(session: Session) -> dict:
    try:
        rows = (
            session.execute(
                text(
                    "SELECT date, spx, splv, sphb, cor10d, cor90d, cor3m, low_beta_state, high_beta_state, "
                    "sphb_resid, label, splv_off_high, abs_break, rel_break, corr_break, beta_frozen, episode_start "
                    "FROM beta_reset_daily ORDER BY date DESC LIMIT 260"
                )
            )
            .mappings()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 — table may not exist before the first beta_reset run
        session.rollback()
        return {"err": str(exc)[:160]}
    pts = []
    for r in reversed(rows):
        pts.append({k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(r).items()})
    out: dict = {"points": pts}
    if rows:
        from trading_intel.market.beta_reset import BetaResetRow, plain_read

        r0 = dict(rows[0])
        br = BetaResetRow(
            date=r0["date"],
            spx=r0["spx"] or 0,
            splv=r0["splv"] or 0,
            sphb=r0["sphb"] or 0,
            cor10d=r0["cor10d"],
            cor90d=r0["cor90d"],
            abs_break=bool(r0["abs_break"]),
            rel_break=bool(r0["rel_break"]),
            corr_break=bool(r0["corr_break"]),
            low_beta_state=r0["low_beta_state"] or "intact",
            episode_start=r0["episode_start"],
            beta_frozen=r0["beta_frozen"],
            sphb_resid=r0["sphb_resid"],
            high_beta_state=r0["high_beta_state"] or "n/a",
            label=r0["label"] or "no reset",
        )
        out["read"] = plain_read(br)
    return out


def write_json(obj: dict, data_dir: Path = DATA_DIR) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    out = data_dir / "voltab.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), default=str), encoding="utf-8")
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- run
def run(
    session: Session,
    *,
    price_source: Any | None = None,
    data_dir: Path = DATA_DIR,
    write: bool = True,
) -> dict:
    bound = log.bind(correlation_id=uuid.uuid4().hex, job="vol_tab")
    vix_rows = load_vix(session)
    vix = {d: v for d, v, _, _ in vix_rows if v is not None}
    spx = load_spx(session, set(vix), price_source)
    ov = vo.compute(vix, spx)
    n_ov = upsert_overshoot(session, ov)
    rec = vo.recovery(ov, spx, vix)

    fed, events = load_events()
    rep = replay_straddles()
    est = map_iv_straddles(session)
    merged = {**est, **rep}
    src = {d: ("replay" if d in rep else "map_iv") for d in merged}
    n_st = upsert_straddles(session, merged, src)
    st_rows = session.execute(
        select(StraddleSnapshot.date, StraddleSnapshot.pct_of_spot, StraddleSnapshot.source)
        .where(StraddleSnapshot.symbol == SYMBOL)
        .order_by(StraddleSnapshot.date)
    ).all()
    st_panel = vt.straddle_panel([(r[0], r[1]) for r in st_rows], fed)
    srcs = {r[0].isoformat(): r[2] for r in st_rows}
    for p in st_panel["points"]:
        p["src"] = srcs.get(p["d"])

    last = ov[-1] if ov else None
    obj = {
        "generated": eastern_now().isoformat(timespec="minutes"),
        "v1": {
            "points": [
                {
                    "d": r.date.isoformat(),
                    "vix": round(r.vix_actual, 2),
                    "model": round(r.vix_model, 2),
                    "res": round(r.residual, 2),
                    "flag": r.flag,
                    "ret": round(r.spx_ret_pct, 3),
                    "beta": round(r.beta, 3),
                }
                for r in ov
            ][-KEEP_POINTS:],
            "recovery": rec,
            "read": vo.plain_read(last),
            "params": {"window": vo.WINDOW, "flag_pts": vo.FLAG_PTS},
        },
        "v2": vt.term_vrp_panel(vix_rows),
        "v3": st_panel,
        "v4": fotm_panel(),
        "v5": color_panel(session),
        "b1": beta_panel(session),
        "events": [e for e in events if e.get("d", "") >= eastern_now().date().isoformat()][:8],
    }
    obj["v2"]["points"] = obj["v2"]["points"][-KEEP_POINTS:]
    obj["v3"]["points"] = obj["v3"]["points"][-KEEP_POINTS:]
    path = write_json(obj, data_dir) if write else None
    bound.info("vol_tab.done", overshoot_rows=n_ov, straddle_rows=n_st, json=str(path))
    return obj


def main() -> None:
    from trading_intel.clients.prices import YFinancePriceSource
    from trading_intel.config import get_settings
    from trading_intel.memory.db import make_session_factory
    from trading_intel.scheduler.jobs._script_report import configure_logging

    configure_logging()
    with make_session_factory(get_settings())() as session:
        obj = run(session, price_source=YFinancePriceSource())
    print("voltab.json written:", obj["v1"]["read"])


if __name__ == "__main__":
    main()
