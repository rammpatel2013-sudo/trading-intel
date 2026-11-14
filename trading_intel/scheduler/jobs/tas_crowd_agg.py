"""Whales vs crowd: sum every SPX/SPXW tape print per minute into ``tas_crowd_minute``.

2026-11-13, backlog #97 (Mithil: "lower the floor, then build #97"). The raw ``tas_prints``
table keeps SPX/SPXW only from $5k premium, so the small "crowd" (1-19 lots) is mostly
missing. Storing every small print raw would add ~1M rows a day to a 3.8 GB NAS, so the
capture job sums them instead: one row per (minute, root, expiry, strike, cp, bucket),
bucket = ``whale`` (size >= ``TAS_WHALE_MIN_SIZE``) or ``crowd``.

Called from ``tas_capture_job.run`` on the tape frame it already pulled: no extra Convex
calls. Consecutive polls overlap (each pulls the newest N prints), so prints are de-duplicated
by their natural key across polls; after a daemon restart the first poll skips prints at or
before the newest ``last_ts`` already stored for the day. Descriptive flow only (rule 4).
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from trading_intel.memory.models import TasCrowdMinute

SPX_ROOTS = frozenset({"SPX", "SPXW"})
_KEY_COLS = ("trade_date", "minute", "root", "expiry", "strike", "cp", "bucket")
_SUM_COLS = ("n_prints", "buy_contracts", "sell_contracts", "other_contracts",
             "buy_premium", "sell_premium", "signed_delta")
_SEEN: deque = deque(maxlen=3)          # print keys of the last 3 polls (daemon = one process)
_CUTOFF: dict = {}                      # trade_date -> newest stored print time at first poll


def reset_state() -> None:
    """Forget the cross-poll de-dup state (tests / a new day)."""
    _SEEN.clear()
    _CUTOFF.clear()


def _cutoff(session: Session, trade_date: date) -> datetime | None:
    if trade_date not in _CUTOFF:
        _CUTOFF[trade_date] = session.execute(
            select(func.max(TasCrowdMinute.last_ts)).where(TasCrowdMinute.trade_date == trade_date)
        ).scalar()
    return _CUTOFF[trade_date]


def summarize(df: pd.DataFrame, *, trade_date: date, whale_min: int = 20,
              seen: set | None = None, cutoff: datetime | None = None) -> tuple[list[dict], set]:
    """Sum the SPX/SPXW prints of one tape frame. Returns (rows, keys of the prints used)."""
    from trading_intel.scheduler.jobs.tas_capture_job import _decode, _f, _i, _norm_cp, _norm_side

    seen = seen or set()
    acc: dict[tuple, dict] = {}
    used: set = set()
    for row in df.itertuples(index=False):
        r = row._asdict()
        raw = r.get("symbol")
        raw = str(raw) if raw is not None and not (isinstance(raw, float) and pd.isna(raw)) else None
        root = r.get("root")
        root = str(root).upper() if isinstance(root, str) else None
        cp = _norm_cp(r.get("cp") if r.get("cp") is not None else r.get("opt_kind"))
        strike = _f(r.get("strike"))
        exp = pd.to_datetime(r.get("expiry") if r.get("expiry") is not None else r.get("expiration"),
                             errors="coerce")
        exp_d = exp.date() if pd.notna(exp) else None
        if (root is None or cp is None or strike is None or exp_d is None) and raw:
            d_root, d_exp, d_cp, d_strike = _decode(raw)
            root = root or (d_root.upper() if d_root else None)
            cp, exp_d = cp or d_cp, exp_d or d_exp
            strike = strike if strike is not None else d_strike
        if root not in SPX_ROOTS or cp is None or strike is None or exp_d is None:
            continue
        price, size = _f(r.get("price")), _i(r.get("size"))
        ts = pd.to_datetime(r.get("time"), errors="coerce")
        if price is None or size is None or size <= 0 or pd.isna(ts):
            continue
        ts = ts.to_pydatetime().replace(tzinfo=None)
        key = (ts, raw or f"{root}{exp_d}{cp}{strike}", price, size)
        if key in seen or key in used or (cutoff is not None and ts <= cutoff):
            continue
        used.add(key)
        bucket = "whale" if size >= whale_min else "crowd"
        k = (trade_date, ts.replace(second=0, microsecond=0), root, exp_d, float(strike), cp, bucket)
        a = acc.get(k)
        if a is None:
            a = acc[k] = dict(zip(_KEY_COLS, k), **{c: 0 for c in _SUM_COLS}, spot=None, last_ts=ts)
        side = _norm_side(r.get("side") if r.get("side") is not None else r.get("aggressor_side"))
        prem = price * size * 100.0
        delta = _f(r.get("delta"))
        a["n_prints"] += 1
        if side == "buy":
            a["buy_contracts"] += size
            a["buy_premium"] += prem
        elif side == "sell":
            a["sell_contracts"] += size
            a["sell_premium"] += prem
        else:
            a["other_contracts"] += size
        if delta is not None and side in ("buy", "sell"):
            a["signed_delta"] += (1 if side == "buy" else -1) * size * delta * 100.0
        spot = _f(r.get("spot"))
        if spot is not None:
            a["spot"] = spot
        a["last_ts"] = max(a["last_ts"], ts)
    return list(acc.values()), used


def capture(session: Session, df: pd.DataFrame, *, trade_date: date, whale_min: int = 20) -> int:
    """Add one poll's SPX/SPXW prints to ``tas_crowd_minute``. Returns rows touched."""
    seen = set().union(*_SEEN) if _SEEN else set()
    rows, used = summarize(df, trade_date=trade_date, whale_min=whale_min, seen=seen,
                           cutoff=_cutoff(session, trade_date))
    _SEEN.append(used)
    if not rows:
        return 0
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    ins = sqlite_insert if dialect == "sqlite" else pg_insert
    for start in range(0, len(rows), 1000):
        stmt = ins(TasCrowdMinute).values(rows[start:start + 1000])
        ex = stmt.excluded
        tbl = TasCrowdMinute.__table__.c
        upd = {c: tbl[c] + ex[c] for c in _SUM_COLS}
        upd["spot"] = func.coalesce(ex.spot, tbl.spot)
        upd["last_ts"] = ex.last_ts  # newest poll wins; prints are time-ordered across polls
        session.execute(stmt.on_conflict_do_update(index_elements=list(_KEY_COLS), set_=upd))
    session.commit()
    return len(rows)
