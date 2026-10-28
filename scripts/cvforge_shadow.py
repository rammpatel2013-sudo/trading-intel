"""CVForge shadow of the per-name Convex collection jobs - COMPARISON ONLY.

Created 2026-10-28. Writes NOTHING to the database.

For every effective watchlist symbol (+ the index roots) it pulls the CVForge chain
ONCE and saves, per symbol, the same aggregate fields ``greeks_snapshot`` stores
(spot, gex/dex/vex/chex totals, gex_flip, dex_flip, atm_iv, volumes) plus GEX by
expiry (the ``gex_rolling`` view) to::

    reports/cvf_shadow/greeks_YYYYMMDD_HHMM.jsonl

``scripts/cvforge_compare.py`` then lines it up against the Convex
``greeks_snapshots`` rows of the same evening.

Why a file and not a table: every reader of ``greeks_snapshots`` (positioning,
sector, MCP regime tools) takes the newest row of ANY source, so shadow rows in that
table would silently replace the Convex numbers on screen. CVForge has its own
Research-tier rate limit, so this costs no Convex budget.

Run (repo root):   python scripts/cvforge_shadow.py [--limit N] [--symbols A,B] [--per-min 300]
NAS:               bash scripts/nas/run_script.sh cvforge_shadow
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_FIELDS = ("spot", "gex_total", "dex_total", "vex_total", "chex_total", "gex_flip",
           "dex_flip", "atm_iv", "call_volume", "put_volume", "call_notional", "put_notional")


def _clean(v):
    """JSON-safe float (NaN/inf -> None)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _symbols(settings, only: list[str] | None) -> list[str]:
    if only:
        return [s.strip().upper() for s in only if s.strip()]
    from trading_intel.memory.db import make_session_factory
    from trading_intel.watchlist import effective_symbols

    with make_session_factory(settings)() as session:
        syms = effective_symbols(session, settings)
    for root in getattr(settings, "index_roots", []) or []:
        if root not in syms:
            syms.append(root)
    return syms


def _gex_by_expiry(df) -> dict:
    """Net gxoi by expiry, calls + / puts - (same sign convention as greeks_snapshot)."""
    if df is None or df.empty or "gxoi" not in df.columns:
        return {}
    import numpy as np

    sign = np.where(df["opt_kind"].astype(str).str.lower().str.startswith("c"), 1.0, -1.0)
    s = (df.assign(_g=sign * df["gxoi"].astype(float))
           .groupby(df["expiration"].dt.date)["_g"].sum())
    return {d.isoformat(): _clean(v) for d, v in s.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=0, help="only the first N symbols (testing)")
    ap.add_argument("--symbols", default="", help="comma list instead of the watchlist")
    ap.add_argument("--per-min", type=int, default=300, help="CVForge pace cap (Research tier allows ~830)")
    ap.add_argument("--out-dir", default="reports/cvf_shadow")
    a = ap.parse_args(argv)

    from trading_intel.clients.cvforge import CVForgeClient
    from trading_intel.config import get_settings
    from trading_intel.timeutils import eastern_now

    settings = get_settings()
    syms = _symbols(settings, a.symbols.split(",") if a.symbols else None)
    if a.limit:
        syms = syms[: a.limit]
    ts = eastern_now().replace(second=0, microsecond=0)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"greeks_{ts:%Y%m%d_%H%M}.jsonl"

    client = CVForgeClient(settings)
    gap = 60.0 / max(1, a.per_min)
    ok = empty = failed = 0
    t_start = time.time()
    with out.open("w", encoding="utf-8") as fh:
        for sym in syms:
            t0 = time.time()
            rec: dict = {"symbol": sym, "ts": ts.isoformat(), "source": "cvforge"}
            try:
                df = client.chain(sym, ref=date.today())
                if df is None or df.empty:
                    rec["error"] = "empty chain"
                    empty += 1
                else:
                    ex = client.exposures(sym, ref=date.today(), chain=df) or {}
                    for k in _FIELDS:
                        rec[k] = _clean(ex.get(k))
                    rec["n_contracts"] = int(len(df))
                    rec["oi_total"] = _clean(df["oi"].fillna(0).sum())
                    rec["gex_by_expiry"] = _gex_by_expiry(df)
                    ok += 1
            except Exception as exc:  # noqa: BLE001 - one bad name must not stop the run
                rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
                failed += 1
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            wait = gap - (time.time() - t0)
            if wait > 0:
                time.sleep(wait)
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass
    print(f"cvforge_shadow: {ok} ok, {empty} empty, {failed} failed of {len(syms)} "
          f"in {time.time() - t_start:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
