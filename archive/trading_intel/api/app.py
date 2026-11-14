"""FastAPI cockpit service — live-ish dealer positioning from the Convex-fed DB.

CVForge is historical, so the cockpit does NOT pull a vendor chain. Each request
reads the snapshot tables the scheduler already fills from Convex (via the MCP
read layer in ``api/positioning.py``), so it adds ZERO Convex calls and the
10/min cap stays reserved for the regime engine (rule 1). DB reads are cheap; a
short TTL cache smooths bursts. Freshness = the scheduler's snapshot cadence
(minutes during RTH), not tick-live.

Run on the NAS (where Postgres lives), reachable over Tailscale:
    uvicorn trading_intel.api.app:app --host 0.0.0.0 --port 8600

Descriptor only — FlashAlpha rule 4.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from trading_intel.api.positioning import build_positioning
from trading_intel.config import get_settings
from trading_intel.memory.db import make_session_factory

_ALLOWED = {"SPX", "SPXW", "SPY", "QQQ"}
_TTL_SECONDS = 20.0
_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="trading-intel cockpit", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # LAN / Tailnet only
    allow_methods=["GET"],
    allow_headers=["*"],
)

_sf = None
_sf_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _factory():
    """Lazily build one shared SQLAlchemy session factory (thread-safe)."""
    global _sf
    if _sf is None:
        with _sf_lock:
            if _sf is None:
                _sf = make_session_factory(get_settings())
    return _sf


def _positioning(symbol: str) -> dict:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(symbol)
        if hit is not None and now - hit[0] < _TTL_SECONDS:
            return hit[1]
    with _factory()() as session:
        payload = build_positioning(session, symbol)
    with _cache_lock:
        _cache[symbol] = (now, payload)
    return payload


@app.get("/positioning/{symbol}")
def positioning(symbol: str) -> dict:
    sym = symbol.upper()
    if sym not in _ALLOWED:
        raise HTTPException(status_code=404, detail=f"symbol {sym} not allowed")
    try:
        return _positioning(sym)
    except Exception as exc:  # noqa: BLE001 — surface any DB/read failure as 503
        raise HTTPException(status_code=503, detail=f"db read failed: {exc}") from exc


@app.get("/healthz")
def healthz() -> dict:
    with _cache_lock:
        cached = sorted(_cache)
    return {"ok": True, "cached": cached, "ttl_s": _TTL_SECONDS, "source": "convex-db"}


@app.get("/")
def index() -> FileResponse:
    page = _STATIC / "cockpit.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="cockpit.html not deployed")
    return FileResponse(page)
