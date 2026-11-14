"""MCP tool ``run_skew_scan`` - on-demand skew-divergence scan (build-queue #102, 2026-11-13).

[Mithil 2026-11-13 19:12] "mcp tool as run the skew scan or run the skew scan on amd or ttd" - on
demand only (the nightly feed + cockpit tab, #101, is parked).

* ``run_skew_scan()``              -> the watchlist (research watchlist + collected tickers, ETFs
  left out): prices first (any session in the last 20 at 10%+ below its 20-day high AND a 10-day
  low), then skew only for those. Returns READY (fired inside the 1-month hold), CLOSE (price
  conditions met, skew just short) and names that can't be scored.
* ``run_skew_scan("AMD, TTD")``    -> those names (any US stock), each with where it stands on the
  three conditions in plain words.

Data: CVForge daily option closes + daily stock bars via ``clients.cvforge`` (rule 1); per-name RR
cache in ``data/cache/skew_hist/`` (first use of a name ~15-30 s, then only new sessions). A run
that needs longer than ~40 s keeps going in a background thread; the next call returns it. Results
are also saved in ``data/cache/skew_scan/``. Read-only: nothing goes to the ``signals`` table.

Wire in server.py:
    from trading_intel.mcp import skew_scan_tool as sst
    sst.register(mcp, settings, session_factory)
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from trading_intel.strategies import skew_divergence as sd
from trading_intel.timeutils import eastern_now

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from sqlalchemy.orm import Session, sessionmaker

    from trading_intel.config import Settings

ROOT = Path(__file__).resolve().parents[2]
HIST_DIR = ROOT / "data" / "cache" / "skew_hist"
SCAN_DIR = ROOT / "data" / "cache" / "skew_scan"
WAIT_S = 40.0
SCAN_BUDGET_S = 25 * 60.0
NAME_BUDGET_S = 150.0
MIN_PRICE = 10.0
NOT_STOCKS = {
    "SPY",
    "QQQ",
    "IWM",
    "SMH",
    "DIA",
    "XLP",
    "XRT",
    "VNQ",
    "MSOS",
    "FAN",
    "DRAM",
    "SPX",
    "SPXW",
    "NDX",
    "VIX",
    "ES",
    "NQ",
    "GLD",
    "SLV",
    "TLT",
    "HYG",
    "XLE",
    "XLF",
    "XLK",
    "XBI",
    "KRE",
    "ARKK",
    "EEM",
    "EFA",
    "FXI",
    "KWEB",
    "USO",
    "UNG",
    "UVXY",
    "SQQQ",
    "TQQQ",
    "SOXL",
    "SOXS",
    "DOGE",
}


def parse_symbols(symbols: str | list[str] | None) -> list[str]:
    if not symbols:
        return []
    items = symbols if isinstance(symbols, list) else re.split(r"[\s,;/]+|\band\b|\bor\b", symbols)
    out: list[str] = []
    for s in items:
        s = (s or "").strip().upper().lstrip("$")
        if s and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) and s not in out:
            out.append(s)
    return out


def _watchlist(session_factory: sessionmaker[Session] | None, exclude: set[str]) -> list[str]:
    from sqlalchemy import select

    from trading_intel.memory.models import Ticker, WatchlistEntry

    if session_factory is None:
        return []
    with session_factory() as s:
        a = s.execute(
            select(WatchlistEntry.symbol).where(WatchlistEntry.active.is_(True))
        ).scalars()
        b = s.execute(select(Ticker.symbol).where(Ticker.is_active.is_(True))).scalars()
        syms = {x.strip().upper() for x in [*a, *b] if x}
    return sorted(x for x in syms if x not in exclude and re.fullmatch(r"[A-Z][A-Z.\-]{0,5}", x))


def _earnings(session_factory: sessionmaker[Session] | None) -> dict[str, str]:
    if session_factory is None:
        return {}
    try:
        from trading_intel.mcp import em_tools as em

        with session_factory() as s:
            ev = em.get_earnings_calendar(s, None, days=35)["events"]
    except Exception:  # noqa: BLE001 - the flag is optional; the scan must still answer
        return {}
    out: dict[str, str] = {}
    for e in ev:
        out.setdefault(e["symbol"], f"{e['date']} {e.get('session') or ''}".strip())
    return out


def scan(
    settings: Settings,
    session_factory: sessionmaker[Session] | None,
    symbols: list[str],
    progress: dict[str, Any],
    *,
    hist: Any = None,  # noqa: ANN401 - RRHistory (injectable for tests)
) -> dict[str, Any]:
    """Run the scan synchronously. ``symbols`` empty = watchlist mode."""
    from trading_intel.vol.rr_history import RRHistory

    t0 = time.time()
    if hist is None:
        from trading_intel.clients.cvforge import CVForgeClient

        hist = RRHistory(CVForgeClient(settings, timeout=15.0), HIST_DIR)
    today = eastern_now().date()
    frm, rr_start = today - timedelta(days=430), today - timedelta(days=410)
    named = bool(symbols)
    exclude = NOT_STOCKS | set(getattr(settings, "chain_exclude_roots", set()) or set())
    universe = symbols or _watchlist(session_factory, exclude)
    progress.update(stage="prices", done=0, of=len(universe))

    def adj(sym: str) -> tuple[str, pd.Series]:
        c = hist.closes(sym, frm, today, adjusted=None)
        progress["done"] += 1
        return sym, pd.Series(c, dtype=float).sort_index()

    with ThreadPoolExecutor(8) as ex:
        px = dict(ex.map(adj, universe))
    no_price = [s for s, c in px.items() if len(c) < 60]
    cands: list[str] = []
    skipped_cheap: list[str] = []
    for s, c in px.items():
        if len(c) < 60:
            continue
        if named:
            cands.append(s)
            continue
        if c.iloc[-1] < MIN_PRICE:
            skipped_cheap.append(s)
            continue
        off = c / c.rolling(sd.HIGH_LOOKBACK, min_periods=1).max() - 1
        low = c <= c.rolling(sd.LOW_LOOKBACK, min_periods=1).min()
        if bool(((off <= sd.DROP_FROM_HIGH) & low).iloc[-sd.HOLD_SESSIONS :].any()):
            cands.append(s)
    progress.update(stage="skew", done=0, of=len(cands))
    reads: dict[str, dict] = {}
    for s in cands:
        progress["current"] = s
        left = SCAN_BUDGET_S - (time.time() - t0)
        if left < 20:
            reads[s] = {
                "verdict": "NOT FINISHED - scan time limit hit; run again (cached names are quick)"
            }
            progress["done"] += 1
            continue
        try:
            raw = hist.closes(s, frm, today, adjusted=False)
            rows = hist.update(s, raw, rr_start, budget_s=min(NAME_BUDGET_S, left))
            rr = pd.Series(
                ((rows["ivp25"] - rows["ivc25"]) * 100).to_numpy(),
                index=list(rows["date"]),
                dtype=float,
            )
            reads[s] = sd.status(sd.evaluate(px[s], rr))
        except TimeoutError:
            reads[s] = {
                "verdict": "NOT FINISHED - option data slow from the vendor; run again later"
            }
        except Exception as exc:  # noqa: BLE001 - one bad name must not sink the scan
            reads[s] = {"verdict": f"CAN'T SCORE - data error: {str(exc)[:120]}"}
        progress["done"] += 1
    earn = _earnings(session_factory)
    for s, r in reads.items():
        if s in earn:
            r["earnings"] = earn[s]
        if named and s in NOT_STOCKS:
            r["note"] = "ETF/index: the rule was proven on stocks; ETFs showed no real edge"
    as_of = max((r.get("as_of", "") for r in reads.values()), default=str(today))
    out: dict[str, Any] = {
        "status": "done",
        "rule": sd.RULE_VERSION,
        "as_of_session": as_of,
        "evidence": sd.EVIDENCE,
        "elapsed_s": round(time.time() - t0, 1),
    }
    if named:
        out["names"] = {
            s: reads.get(s, {"verdict": "CAN'T SCORE - no price data"}) for s in symbols
        }
        return out
    ready = {s: r for s, r in reads.items() if r.get("verdict", "").startswith("READY")}
    out["ready"] = dict(sorted(ready.items(), key=lambda kv: kv[1]["signal"]["sessions_ago"]))
    out["close"] = {s: r for s, r in reads.items() if r.get("verdict", "").startswith("CLOSE")}
    out["cant_score"] = sorted(
        s for s, r in reads.items() if r.get("verdict", "").startswith("CAN'T")
    )
    out["not_finished"] = sorted(
        s for s, r in reads.items() if r.get("verdict", "").startswith("NOT FINISHED")
    )
    out["checked"] = {
        "watchlist_names": len(universe),
        "passed_price_check": len(cands),
        "no_price_data": len(no_price),
        "under_$10_skipped": len(skipped_cheap),
    }
    return out


def register(
    mcp: FastMCP,
    settings: Settings,
    session_factory: sessionmaker[Session] | None,
    *,
    scanner: Callable[..., dict[str, Any]] | None = None,
    wait_s: float = WAIT_S,
    save_dir: Path | None = SCAN_DIR,
) -> Callable[..., dict[str, Any]]:
    """Register ``run_skew_scan``; returns the plain function (tests call it directly)."""
    run = scanner or (lambda syms, prog: scan(settings, session_factory, syms, prog))
    jobs: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def _file(key: str) -> Path | None:
        return (
            None if save_dir is None else save_dir / (re.sub(r"[^A-Z0-9_-]+", "_", key) + ".json")
        )

    def run_skew_scan(symbols: str = "", refresh: bool = False) -> dict[str, Any]:
        """Skew-divergence scan (stocks): which names are READY on our skew signal.

        - No symbols ("run the skew scan") = the whole watchlist: READY names (fired in the last
          20 sessions = inside the ~1-month hold), CLOSE names, names that can't be scored.
        - symbols="AMD, TTD" = those names, each with where it stands on the three conditions
          (10%+ below 20-day high, 10-day low, skew moving to calls) in plain words.
        Rule sd-v1, evidence included. First run of a name pulls a year of option closes from
        CVForge; if a run needs more than ~40 s it keeps going and returns "running" - call again
        in a few minutes. ``refresh=True`` re-runs even if today's result exists. Read-only.
        """
        syms = parse_symbols(symbols)
        day = eastern_now().date()
        key = f"{day:%Y%m%d}_{'-'.join(sorted(syms)) or 'WATCHLIST'}"
        with lock:
            job = jobs.get(key)
            f = _file(key)
            if job is None and not refresh and f is not None and f.exists():
                return {**json.loads(f.read_text(encoding="utf-8")), "from_saved_run": True}
            if job is None or (refresh and job["thread"] and not job["thread"].is_alive()):
                job = {"progress": {}, "result": None, "error": None, "started": time.time()}

                def work(j: dict[str, Any] = job) -> None:
                    try:
                        j["result"] = run(syms, j["progress"])
                        g = _file(key)
                        if g is not None:
                            g.parent.mkdir(parents=True, exist_ok=True)
                            g.write_text(json.dumps(j["result"], default=str), encoding="utf-8")
                    except Exception as exc:  # noqa: BLE001 - reported back to the caller
                        j["error"] = str(exc)[:300]

                job["thread"] = threading.Thread(target=work, daemon=True)
                jobs[key] = job
                job["thread"].start()
        job["thread"].join(timeout=wait_s)
        if job["error"]:
            jobs.pop(key, None)
            return {"status": "error", "error": job["error"]}
        if job["result"] is not None:
            return job["result"]
        p = job["progress"]
        return {
            "status": "running",
            "message": "Still pulling option history (first runs take longest). Ask again in a "
            "few minutes and the result will be ready.",
            "stage": p.get("stage"),
            "done": p.get("done"),
            "of": p.get("of"),
            "current": p.get("current"),
            "running_for_s": round(time.time() - job["started"]),
        }

    mcp.tool()(run_skew_scan)
    return run_skew_scan
