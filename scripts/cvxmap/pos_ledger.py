#!/usr/bin/env python
"""Signed-position ledger builder — stage-2 nightly job.

Reads the raw NDJSON dump (written by cvx_collect.py), extracts the signed
flow per contract at the last good snapshot (16:20–17:00 ET), and writes:
  1. pos_days  — one row per (trade_date, expiry, strike, right)
  2. pos_ledger — running signed position as of next morning

IDEMPOTENT: uses INSERT ... ON CONFLICT DO NOTHING for pos_days and
upserts for pos_ledger.  Safe to re-run.

Stage-2 transform: reads only the banked raw dump, NEVER the Convex API.
MAP rule: this is a SECOND VIEW beside the frozen map — naive OI stays
the base view and is never replaced.

Run:
    python pos_ledger.py                   # today's date
    python pos_ledger.py --date 20261028   # specific date
    python pos_ledger.py --backfill 7      # last N days
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import math
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# ---------- env + DB setup (same pattern as am_feed.py) ----------
_TI_DEFAULT = r"C:\Users\drmit\PycharmProjects\trading-intel"
TI = os.environ.get("TI_DIR") or _TI_DEFAULT

ENV_CANDIDATES = [
    "/app/.env",
    r"C:\Users\drmit\PycharmProjects\trading-intel\.env",
    os.path.join(HERE, ".env"),
    os.path.expanduser("~/mnt/trading-intel/.env"),
]

# Reuse the collector's decode() for raw data parsing
sys.path.insert(0, HERE)
from cvx_live import decode, exp_date  # noqa: E402

# Ledger parameters (locked 2026-11-03)
DEPTH = 11              # expiry indexes 0..11
SYMBOL = "SPX"          # root symbol (SPXW contracts appear as SPX in the chain)
STRIKE_RANGE = 250      # +-250 pts from spot
SNAP_WINDOW = (16, 20)  # (hour, minute) ET — start of the snapshot window
GATE_THRESHOLD = 0.15   # |net|/total within cluster for spread label
CLUSTER_WIDTH = 100     # pts


# ---------- Eastern time helpers ----------
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:
    _ET = None


def now_et():
    if _ET is not None:
        return datetime.datetime.now(_ET).replace(tzinfo=None)
    return datetime.datetime.utcnow() - datetime.timedelta(hours=4)


def log(msg):
    stamp = now_et().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}  [pos_ledger] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, f"pos_ledger_{now_et().strftime('%Y%m%d')}.log"),
                  "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ---------- DB engine ----------
def get_engine():
    """Build a SQLAlchemy engine from env, same pattern as run_cvxmap.sh."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        for p in ENV_CANDIDATES:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith("DATABASE_URL="):
                        db_url = s.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                    if s.startswith("NAS_DATABASE_URL=") and not db_url:
                        db_url = s.split("=", 1)[1].strip().strip('"').strip("'")
            if db_url:
                break
    if not db_url:
        raise SystemExit("no DATABASE_URL found in env or .env files")
    from sqlalchemy import create_engine
    return create_engine(db_url, pool_pre_ping=True)


# ---------- raw data extraction ----------
def read_raw(raw_path: str):
    """Yield records from a raw_YYYYMMDD.ndjson.gz file one at a time.
    2026-11-05: was a list of the whole day - died silently (OOM, 3.8 GB NAS) on 11/03 and 11/04."""
    opener = gzip.open if raw_path.endswith(".gz") else open
    with opener(raw_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _abs(x):
    """Absolute value, handling None."""
    return abs(x) if x is not None and x == x else 0.0


def extract_day_flow(
    records,
    trade_date: datetime.date,
) -> dict[tuple, dict]:
    """Extract signed flow per contract from the raw records for one day.

    Returns {(expiry_date, strike, right): {vb, vs, vol, gb, gs, db, ds, val_b, val_s,
    oi_am, snap_ts, n_snaps, dte}} using the LAST good snapshot in the 16:20–17:00 window.
    If no snapshots in that window, uses the absolute last snapshot of the day.
    """
    # First pass: collect all snapshots per contract, tracking the best one
    # Key: (expiry_iso, strike, right)
    contracts: dict[tuple, dict] = {}
    first_oi: dict[tuple, int | None] = {}  # OI from the first pull (morning OI)
    snap_count: dict[tuple, int] = {}

    for rec in records:
        if rec.get("pull") != "idx":
            continue
        ts_str = rec.get("ts", "")
        try:
            ts = datetime.datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue

        # Only process records from SPX
        if SYMBOL not in (rec.get("symbols") or []):
            continue

        dec = decode(rec)
        d = dec.get(SYMBOL)
        if not d or not d["exps"]:
            continue

        spot = d["spot"]
        hh, mm = ts.hour, ts.minute
        in_window = (hh > SNAP_WINDOW[0]) or (hh == SNAP_WINDOW[0] and mm >= SNAP_WINDOW[1])

        for head, book in d["exps"]:
            exp_iso = exp_date(head)
            try:
                exp_d = datetime.date.fromisoformat(exp_iso)
            except (ValueError, TypeError):
                continue
            dte = (exp_d - trade_date).days
            if dte < 0 or dte > DEPTH:
                continue

            for k, (c, p) in book.items():
                if abs(k - spot) > STRIKE_RANGE:
                    continue

                for right_label, side in [("C", c), ("P", p)]:
                    key = (exp_iso, k, right_label)

                    # Track OI from the first pull (morning OI proxy)
                    oi_val = side.get("oi")
                    if key not in first_oi and oi_val is not None:
                        try:
                            first_oi[key] = int(float(oi_val))
                        except (ValueError, TypeError):
                            first_oi[key] = None

                    snap_count[key] = snap_count.get(key, 0) + 1

                    # Extract signed flow fields
                    vb = _abs(side.get("volm_buy"))
                    vs = _abs(side.get("volm_sell"))
                    vol_total = _abs(side.get("volm")) or (vb + vs)
                    gb = _abs(side.get("gammas_buy"))
                    gs = _abs(side.get("gammas_sell"))
                    db_val = _abs(side.get("deltas_buy"))
                    ds_val = _abs(side.get("deltas_sell"))
                    val_b = _abs(side.get("value_buy"))
                    val_s = _abs(side.get("value_sell"))

                    entry = {
                        "vb": vb, "vs": vs, "vol": vol_total,
                        "gb": gb, "gs": gs, "db": db_val, "ds": ds_val,
                        "val_b": val_b, "val_s": val_s,
                        "snap_ts": ts, "dte": dte,
                        "in_window": in_window,
                    }

                    prev = contracts.get(key)
                    if prev is None:
                        contracts[key] = entry
                    elif in_window and not prev.get("in_window"):
                        # Prefer window snapshot over pre-window
                        contracts[key] = entry
                    elif in_window == prev.get("in_window"):
                        # Within same zone, take the latest
                        if ts >= prev["snap_ts"]:
                            contracts[key] = entry

    # Build the result
    result = {}
    for key, entry in contracts.items():
        exp_iso, strike, right = key
        result[key] = {
            "vb": entry["vb"],
            "vs": entry["vs"],
            "vol": entry["vol"],
            "gb": entry["gb"],
            "gs": entry["gs"],
            "db": entry["db"],
            "ds": entry["ds"],
            "val_b": entry["val_b"],
            "val_s": entry["val_s"],
            "oi_am": first_oi.get(key),
            "snap_ts": entry["snap_ts"],
            "n_snaps": snap_count.get(key, 0),
            "dte": entry["dte"],
        }

    return result


# ---------- DB writes ----------
def write_pos_days(engine, trade_date: datetime.date, flow: dict[tuple, dict]) -> int:
    """Write pos_days rows. Returns count of rows inserted."""
    from sqlalchemy import text

    upsert_sql = text("""
        INSERT INTO pos_days
            (symbol, trade_date, expiry, strike, "right", dte_at_date,
             vb, vs, vol, gb, gs, db, ds, val_b, val_s, oi_am, snap_ts, n_snaps)
        VALUES
            (:symbol, :trade_date, :expiry, :strike, :right, :dte_at_date,
             :vb, :vs, :vol, :gb, :gs, :db, :ds, :val_b, :val_s, :oi_am, :snap_ts, :n_snaps)
        ON CONFLICT ON CONSTRAINT uq_pos_days DO NOTHING
    """)

    inserted = 0
    with engine.begin() as conn:
        for (exp_iso, strike, right), entry in flow.items():
            try:
                exp_d = datetime.date.fromisoformat(exp_iso)
            except (ValueError, TypeError):
                continue
            result = conn.execute(upsert_sql, {
                "symbol": SYMBOL,
                "trade_date": trade_date,
                "expiry": exp_d,
                "strike": strike,
                "right": right,
                "dte_at_date": entry["dte"],
                "vb": entry["vb"],
                "vs": entry["vs"],
                "vol": entry["vol"],
                "gb": entry["gb"],
                "gs": entry["gs"],
                "db": entry["db"],
                "ds": entry["ds"],
                "val_b": entry["val_b"],
                "val_s": entry["val_s"],
                "oi_am": entry["oi_am"],
                "snap_ts": entry["snap_ts"],
                "n_snaps": entry["n_snaps"],
            })
            inserted += result.rowcount
    return inserted


def update_pos_ledger(
    engine,
    trade_date: datetime.date,
    flow: dict[tuple, dict],
) -> int:
    """Update pos_ledger running state for the next morning.

    For each contract in today's flow:
    - pos_raw += (vb - vs)  [pure accumulation]
    - pos_oi: OI-reconciled variant using dOI = oi_next_morning - oi_this_morning
      (deferred until next morning's OI is available; for now seed with pos_raw)

    Returns count of rows upserted.
    """
    from sqlalchemy import text

    next_morning = trade_date + datetime.timedelta(days=1)
    # Skip to Monday if Friday
    if trade_date.weekday() == 4:
        next_morning = trade_date + datetime.timedelta(days=3)

    # Load prior ledger state for contracts we're updating
    prior_sql = text("""
        SELECT expiry, strike, "right", pos_raw, pos_oi, coverage_days,
               days_since_listing, seed_gap, oi_current
        FROM pos_ledger
        WHERE symbol = :symbol
          AND as_of_date = :as_of_date
          AND expiry = :expiry
          AND strike = :strike
          AND "right" = :right
    """)

    upsert_sql = text("""
        INSERT INTO pos_ledger
            (symbol, as_of_date, expiry, strike, "right", dte_at_date,
             pos_raw, pos_oi, coverage_days, days_since_listing, seed_gap,
             low_confidence, oi_prior, oi_current)
        VALUES
            (:symbol, :as_of_date, :expiry, :strike, :right, :dte_at_date,
             :pos_raw, :pos_oi, :coverage_days, :days_since_listing, :seed_gap,
             :low_confidence, :oi_prior, :oi_current)
        ON CONFLICT ON CONSTRAINT uq_pos_ledger
        DO UPDATE SET
            pos_raw = EXCLUDED.pos_raw,
            pos_oi = EXCLUDED.pos_oi,
            coverage_days = EXCLUDED.coverage_days,
            days_since_listing = EXCLUDED.days_since_listing,
            seed_gap = EXCLUDED.seed_gap,
            low_confidence = EXCLUDED.low_confidence,
            oi_prior = EXCLUDED.oi_prior,
            oi_current = EXCLUDED.oi_current
    """)

    upserted = 0
    with engine.begin() as conn:
        for (exp_iso, strike, right), entry in flow.items():
            try:
                exp_d = datetime.date.fromisoformat(exp_iso)
            except (ValueError, TypeError):
                continue

            # DTE as of next morning
            dte_next = (exp_d - next_morning).days
            if dte_next < 0:
                continue  # already expired

            # Customer net for the day
            net_day = (entry["vb"] or 0.0) - (entry["vs"] or 0.0)

            # Look up prior state (from trade_date morning, i.e., today's opening state)
            prior = conn.execute(prior_sql, {
                "symbol": SYMBOL,
                "as_of_date": trade_date,
                "expiry": exp_d,
                "strike": strike,
                "right": right,
            }).fetchone()

            if prior is not None:
                old_raw = prior.pos_raw or 0.0
                old_oi_pos = prior.pos_oi or 0.0
                old_coverage = prior.coverage_days or 0
                old_days_since = prior.days_since_listing or 0
                old_seed_gap = prior.seed_gap
                old_oi_current = prior.oi_current
            else:
                old_raw = 0.0
                old_oi_pos = 0.0
                old_coverage = 0
                old_days_since = 0
                old_seed_gap = True  # first appearance → seed gap
                old_oi_current = None

            # pos_raw: += net_day
            new_raw = old_raw + net_day

            # pos_oi: OI reconciliation
            # dOI = today's morning OI (from pos_day.oi_am) vs prior state's oi_current
            # For now, if we have both OI values, reconcile; otherwise fall back to pos_raw
            oi_am_today = entry["oi_am"]
            low_conf = False

            if old_oi_current is not None and oi_am_today is not None:
                # We have dOI data — but note this is comparing THIS morning's OI to
                # the prior ledger's OI, which may itself be from this morning.
                # The proper reconciliation needs NEXT morning's OI, so for now
                # we use pos_raw and flag for reconciliation in the morning job.
                new_oi_pos = old_oi_pos + net_day
            else:
                new_oi_pos = old_oi_pos + net_day

            # Flag low confidence if volume is very small
            total_vol = (entry["vb"] or 0.0) + (entry["vs"] or 0.0)
            if total_vol < 10:
                low_conf = True

            result = conn.execute(upsert_sql, {
                "symbol": SYMBOL,
                "as_of_date": next_morning,
                "expiry": exp_d,
                "strike": strike,
                "right": right,
                "dte_at_date": dte_next,
                "pos_raw": new_raw,
                "pos_oi": new_oi_pos,
                "coverage_days": old_coverage + 1,
                "days_since_listing": old_days_since + 1,
                "seed_gap": old_seed_gap if old_coverage > 0 else True,
                "low_confidence": low_conf,
                "oi_prior": old_oi_current,
                "oi_current": oi_am_today,
            })
            upserted += result.rowcount

    return upserted


def reconcile_oi(engine, as_of_date: datetime.date) -> int:
    """Morning reconciliation: update pos_oi using the fresh OI from today's first pull.

    Called in the morning AFTER the collector's first pull has landed a raw record
    with the overnight OI.  Reads the first pull's OI for each contract and
    reconciles against the ledger's oi_current.

    Returns count of rows reconciled.
    """
    from sqlalchemy import text

    # Read the morning's raw to get fresh OI
    raw_path = os.path.join(DATA, f"raw_{as_of_date.strftime('%Y%m%d')}.ndjson.gz")
    if not os.path.exists(raw_path):
        # Try uncompressed
        raw_path = os.path.join(DATA, f"raw_{as_of_date.strftime('%Y%m%d')}.ndjson")
        if not os.path.exists(raw_path):
            log(f"no raw file for {as_of_date} — skipping OI reconciliation")
            return 0

    # Just read the first idx record to get morning OI
    opener = gzip.open if raw_path.endswith(".gz") else open
    morning_oi: dict[tuple, int] = {}
    with opener(raw_path, "rt", encoding="utf-8") as fh:
        for line_str in fh:
            line_str = line_str.strip()
            if not line_str:
                continue
            try:
                rec = json.loads(line_str)
            except json.JSONDecodeError:
                continue
            if rec.get("pull") != "idx":
                continue
            if SYMBOL not in (rec.get("symbols") or []):
                continue

            dec = decode(rec)
            d = dec.get(SYMBOL)
            if not d or not d["exps"]:
                continue

            for head, book in d["exps"]:
                exp_iso = exp_date(head)
                for k, (c, p) in book.items():
                    for right_label, side in [("C", c), ("P", p)]:
                        oi_val = side.get("oi")
                        if oi_val is not None:
                            key = (exp_iso, k, right_label)
                            if key not in morning_oi:
                                morning_oi[key] = int(float(oi_val))
            break  # only need the first pull

    if not morning_oi:
        log("no morning OI found in first pull — skipping reconciliation")
        return 0

    # Update ledger rows for this morning
    update_sql = text("""
        UPDATE pos_ledger
        SET oi_current = :oi_current,
            pos_oi = CASE
                WHEN oi_prior IS NOT NULL AND :oi_current IS NOT NULL THEN
                    CASE
                        WHEN :oi_current - COALESCE(oi_prior, 0) > 0 THEN
                            COALESCE(pos_oi, 0) + SIGN(COALESCE(pos_raw, 0)) *
                                LEAST(ABS(COALESCE(pos_raw, 0)), :oi_current - COALESCE(oi_prior, 0))
                        ELSE
                            -- Closing flow: move toward zero
                            CASE
                                WHEN ABS(COALESCE(pos_oi, 0)) > ABS(COALESCE(oi_prior, 0) - :oi_current)
                                THEN pos_oi - SIGN(COALESCE(pos_oi, 0)) *
                                    LEAST(ABS(COALESCE(pos_oi, 0)), ABS(COALESCE(oi_prior, 0) - :oi_current))
                                ELSE 0
                            END
                    END
                ELSE pos_oi
            END,
            low_confidence = CASE
                WHEN oi_prior IS NOT NULL AND :oi_current IS NOT NULL
                     AND ABS(:oi_current - COALESCE(oi_prior, 0)) > COALESCE(
                         (SELECT vol FROM pos_days pd
                          WHERE pd.symbol = pos_ledger.symbol
                            AND pd.expiry = pos_ledger.expiry
                            AND pd.strike = pos_ledger.strike
                            AND pd."right" = pos_ledger."right"
                            AND pd.trade_date = :prior_date), 0)
                THEN TRUE
                ELSE low_confidence
            END
        WHERE symbol = :symbol
          AND as_of_date = :as_of_date
          AND expiry = :expiry
          AND strike = :strike
          AND "right" = :right
    """)

    prior_date = as_of_date - datetime.timedelta(days=1)
    if as_of_date.weekday() == 0:  # Monday
        prior_date = as_of_date - datetime.timedelta(days=3)

    reconciled = 0
    with engine.begin() as conn:
        for (exp_iso, strike, right), oi_val in morning_oi.items():
            try:
                exp_d = datetime.date.fromisoformat(exp_iso)
            except (ValueError, TypeError):
                continue

            result = conn.execute(update_sql, {
                "symbol": SYMBOL,
                "as_of_date": as_of_date,
                "expiry": exp_d,
                "strike": strike,
                "right": right,
                "oi_current": oi_val,
                "prior_date": prior_date,
            })
            reconciled += result.rowcount

    return reconciled


# ---------- JSON export for cockpit ----------
def export_ledger_json(engine, as_of_date: datetime.date) -> str:
    """Export the latest ledger state as JSON for the cockpit panel.

    Returns path to the written file.
    """
    from sqlalchemy import text

    sql = text("""
        SELECT expiry, strike, "right", dte_at_date, pos_raw, pos_oi,
               coverage_days, days_since_listing, seed_gap, low_confidence,
               oi_prior, oi_current
        FROM pos_ledger
        WHERE symbol = :symbol AND as_of_date = :as_of_date
        ORDER BY expiry, strike, "right"
    """)

    rows = []
    with engine.connect() as conn:
        for r in conn.execute(sql, {"symbol": SYMBOL, "as_of_date": as_of_date}):
            rows.append({
                "exp": r.expiry.isoformat(),
                "k": r.strike,
                "r": r.right,
                "dte": r.dte_at_date,
                "raw": round(r.pos_raw or 0, 1),
                "oi": round(r.pos_oi or 0, 1),
                "cov": r.coverage_days,
                "dsl": r.days_since_listing,
                "gap": bool(r.seed_gap),
                "lo": bool(r.low_confidence),
                "oi0": r.oi_prior,
                "oi1": r.oi_current,
            })

    # Also pull today's pos_days for the daily flow column
    day_sql = text("""
        SELECT expiry, strike, "right", vb, vs, vol, gb, gs, db, ds, val_b, val_s, oi_am
        FROM pos_days
        WHERE symbol = :symbol AND trade_date = :trade_date
        ORDER BY expiry, strike, "right"
    """)

    day_rows = {}
    with engine.connect() as conn:
        for r in conn.execute(day_sql, {"symbol": SYMBOL, "trade_date": as_of_date}):
            key = f"{r.expiry.isoformat()}|{r.strike}|{r.right}"
            day_rows[key] = {
                "vb": round(r.vb or 0, 1),
                "vs": round(r.vs or 0, 1),
                "vol": round(r.vol or 0, 1),
                "net": round((r.vb or 0) - (r.vs or 0), 1),
            }

    # Compute gate ratio per 100-pt cluster
    clusters: dict[tuple, list] = {}
    for row in rows:
        exp = row["exp"]
        cluster_base = int(row["k"] // CLUSTER_WIDTH) * CLUSTER_WIDTH
        ckey = (exp, cluster_base, row["r"])
        clusters.setdefault(ckey, []).append(row)

    for ckey, members in clusters.items():
        total_net = sum(abs(m["raw"]) for m in members)
        total_gross = sum(abs(m["raw"]) for m in members)  # same for unsigned
        # Gate: |sum(raw)| / sum(|raw|)
        signed_sum = sum(m["raw"] for m in members)
        if total_gross > 0:
            gate = abs(signed_sum) / total_gross
        else:
            gate = 0.0
        label = "directional" if gate > GATE_THRESHOLD else "balanced"
        for m in members:
            m["gate"] = round(gate, 3)
            m["slab"] = label

    obj = {
        "as_of": as_of_date.isoformat(),
        "symbol": SYMBOL,
        "n_rows": len(rows),
        "rows": rows,
        "today": day_rows,
    }

    out_path = os.path.join(DATA, "ledger.json")
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, out_path)
    # 2026-11-04: dated copy so the nightly replay can load the ledger AS IT STOOD that morning (no look-ahead);
    # keep ~60 days of copies.
    try:
        import glob as _glob
        import shutil as _shutil
        _shutil.copyfile(out_path, os.path.join(DATA, "ledger_%s.json" % as_of_date.strftime("%Y%m%d")))
        cutoff = (as_of_date - datetime.timedelta(days=60)).strftime("%Y%m%d")
        for f in _glob.glob(os.path.join(DATA, "ledger_2*.json")):
            if os.path.basename(f)[7:15] < cutoff:
                os.remove(f)
    except Exception as exc:
        log(f"  dated ledger copy failed: {exc}")
    log(f"  wrote {out_path} ({len(rows)} ledger rows, {len(day_rows)} day rows)")
    return out_path


# ---------- main ----------
def process_date(engine, trade_date: datetime.date) -> None:
    """Process one day: extract flow from raw, write pos_days, update pos_ledger."""
    raw_path = os.path.join(DATA, f"raw_{trade_date.strftime('%Y%m%d')}.ndjson.gz")
    if not os.path.exists(raw_path):
        raw_path = os.path.join(DATA, f"raw_{trade_date.strftime('%Y%m%d')}.ndjson")
        if not os.path.exists(raw_path):
            log(f"no raw file for {trade_date} — skipping")
            return

    log(f"processing {trade_date}  raw={os.path.basename(raw_path)}")

    # Step 1: read raw records
    # Step 2: extract day flow (streams the raw file - no whole-day list in memory)
    flow = extract_day_flow(read_raw(raw_path), trade_date)
    if not flow:
        log("  no SPX contracts in the raw file - skipping")
        return
    n_calls = sum(1 for k in flow if k[2] == "C")
    n_puts = sum(1 for k in flow if k[2] == "P")
    log(f"  {len(flow)} contracts ({n_calls}C + {n_puts}P)")

    # Step 3: put field sign check (Step 0 blocker)
    # Verify that vb/vs are non-negative after abs() correction
    neg_vb = sum(1 for v in flow.values() if (v["vb"] or 0) < 0)
    neg_vs = sum(1 for v in flow.values() if (v["vs"] or 0) < 0)
    if neg_vb or neg_vs:
        log(f"  WARNING: {neg_vb} contracts with negative vb, {neg_vs} with negative vs")

    # Step 4: write pos_days
    inserted = write_pos_days(engine, trade_date, flow)
    log(f"  pos_days: {inserted} rows inserted")

    # Step 5: update pos_ledger
    upserted = update_pos_ledger(engine, trade_date, flow)
    log(f"  pos_ledger: {upserted} rows upserted")

    # Step 6: export ledger.json for the cockpit
    next_morning = trade_date + datetime.timedelta(days=1)
    if trade_date.weekday() == 4:
        next_morning = trade_date + datetime.timedelta(days=3)
    export_ledger_json(engine, next_morning)


def main():
    parser = argparse.ArgumentParser(description="Signed-position ledger builder")
    parser.add_argument("--date", help="Process specific date (YYYYMMDD)")
    parser.add_argument("--backfill", type=int, help="Process last N days")
    parser.add_argument("--reconcile", help="Run OI reconciliation for date (YYYYMMDD)")
    parser.add_argument("--export", help="Export ledger.json for date (YYYYMMDD), no processing")
    args = parser.parse_args()

    engine = get_engine()
    log("=" * 60)

    if args.export:
        d = datetime.datetime.strptime(args.export, "%Y%m%d").date()
        log(f"export-only for {d}")
        export_ledger_json(engine, d)
        return

    if args.reconcile:
        d = datetime.datetime.strptime(args.reconcile, "%Y%m%d").date()
        log(f"OI reconciliation for {d}")
        n = reconcile_oi(engine, d)
        log(f"reconciled {n} rows")
        return

    if args.date:
        d = datetime.datetime.strptime(args.date, "%Y%m%d").date()
        process_date(engine, d)
    elif args.backfill:
        today = now_et().date()
        for i in range(args.backfill, 0, -1):
            d = today - datetime.timedelta(days=i)
            if d.weekday() < 5:  # skip weekends
                process_date(engine, d)
    else:
        # Default: today
        process_date(engine, now_et().date())

    log("done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("interrupted by user")
    except Exception:
        log("FATAL\n" + traceback.format_exc())
        sys.exit(1)
