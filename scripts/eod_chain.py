"""eod_chain.py - created 2026-11-20 (night). End-of-day FULL SPX chain from Convex for the position ledger.

Why: the minute collector (cvx_collect.py, locked) pulls SPX with 12 expiries at +-3.5% (~+-250 pts), so the
ledger can only sign contracts inside that window (31-45% of 0DTE OI near spot on 11/06-11/19). Convex's
volm_buy / volm_sell / value_buy / value_sell are DAY counters (reset ~18:00 ET), so ONE pull after the close
gives the whole day's signed volume for every contract. Probed 11/20 20:49: SPX expiries 0-39 + 40-79 at
+-30% = 60 expiries to Dec 2031, 13,806 strikes, 15 MB, 4 s, PARAMS width 48 OK.

Role: BACKUP ledger source (Mithil 11/20: Convex and optionsdata.ai are the same feed; optionsdata.ai's od_nightly
is the primary full-chain source). This file is banked every day so a failed od_nightly day is not lost.

STANDALONE. Imports cvx_collect only for login / PARAMS / creds (that file is not changed). Writes ONE file:
scripts/cvxmap/data/eod_raw_YYYYMMDD.ndjson.gz (records in the same shape as raw_*.ndjson.gz, pull="eod"),
which nothing that reads raw_*.ndjson.gz - the Map included - ever opens (MAP rule).

Budget: Convex pro = 7 requests/min shared (collector <= 3, NAS tape 2, blocks ~1). This job makes ONE request
per minute (>= 65 s apart), so the shared total stays <= 7.

Run (NAS, DSM "TI eod chain" Mon-Fri 16:25):
    bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_script.sh eod_chain
    ... eod_chain --force          # outside the 16:15-17:55 window (counters may be zero after ~18:00)
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CVX = os.path.join(HERE, "cvxmap")
sys.path.insert(0, CVX)

SYMBOL = "SPX"
RNG = 0.50                     # +-50% of spot: +-30% held 86% of SPX OI / 98.5% of volume on 11/20, +-50% ~94% OI
CHUNKS = [list(range(0, 40)), list(range(40, 80))]   # 11/20: 40 + 20 expiries; an empty second chunk is fine
GAP_S = 65                     # one request a minute
WINDOW = ((16, 15), (17, 55))  # after the 16:15 settlement, before the ~18:00 counter reset


def in_window(now: datetime.datetime) -> bool:
    t = (now.hour, now.minute)
    return now.weekday() < 5 and WINDOW[0] <= t <= WINDOW[1]


def out_path(data_dir: str, now: datetime.datetime) -> str:
    return os.path.join(data_dir, "eod_raw_%s.ndjson.gz" % now.strftime("%Y%m%d"))


def width_ok(data, n_params: int) -> bool | None:
    """True/False when the first contract row can be checked against PARAMS, None when there is nothing to check."""
    try:
        if data and data[0].get("chain"):
            return len(data[0]["chain"][0][1][0][1]) - 1 == n_params
    except Exception:
        return False
    return None


def summarize(rec: dict) -> dict:
    """Expiries / strike rows / any non-zero buy-sell counters in one banked record (decoded with the writer's own decoder)."""
    import cvx_live
    d = cvx_live.decode(rec).get(SYMBOL) or {}
    exps = d.get("exps") or []
    n = sum(len(b) for _, b in exps)
    nz = 0
    for _h, b in exps:
        for _k, (c, p) in b.items():
            for side in (c, p):
                if (side.get("volm_buy") or 0) or (side.get("volm_sell") or 0):
                    nz += 1
    dates = [cvx_live.exp_date(h) for h, _ in exps]
    return {"n_exp": len(exps), "strikes": n, "contracts_with_signed_volume": nz,
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run outside the 16:15-17:55 ET window")
    args = ap.parse_args(argv)
    os.chdir(CVX)
    import cvx_collect as C
    now = C.now_et()
    if not args.force and not in_window(now):
        C.log("eod_chain: outside 16:15-17:55 ET on a weekday - nothing to do (use --force)")
        return 0
    path = out_path(C.DATA, now)
    os.makedirs(C.DATA, exist_ok=True)
    S = C.login()
    ok = 0
    for i, exps in enumerate(CHUNKS):
        if i:
            time.sleep(GAP_S)
        body = {"symbols": [SYMBOL], "params": C.PARAMS, "exps": exps, "rng": RNG}
        j, el, nb = None, 0.0, 0
        for attempt in (1, 2):
            try:
                t0 = time.time()
                r = S.post(C.BASE + "/api/core/get/chain", json=body, timeout=180)
                if r.status_code == 401:
                    S = C.login(); raise RuntimeError("401 - re-login")
                r.raise_for_status()
                j, el, nb = r.json(), time.time() - t0, len(r.content)
                break
            except Exception as exc:
                C.log("eod_chain chunk %d attempt %d failed: %s" % (i, attempt, str(exc)[:160]))
                if attempt == 1:
                    time.sleep(GAP_S)
        if j is None:
            continue
        w = width_ok(j.get("data"), len(C.PARAMS))
        if w is False:
            C.log("eod_chain chunk %d PARAM WIDTH MISMATCH - not banked" % i)
            continue
        rec = {"ts": C.now_et().isoformat(timespec="seconds"), "pull": "eod", "symbols": [SYMBOL],
               "exps": exps, "rng": RNG, "params": C.PARAMS, "elapsed_s": round(el, 3), "bytes": nb,
               "data": j.get("data")}
        with gzip.open(path, "at", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        try:
            s = summarize(rec)
        except Exception as exc:
            s = {"summary_error": str(exc)[:120]}
        C.log("eod_chain chunk %d: %.1f s %.1f MB %s" % (i, el, nb / 1e6, json.dumps(s)))
        ok += 1
    C.log("eod_chain done: %d/%d chunks banked -> %s" % (ok, len(CHUNKS), path))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
