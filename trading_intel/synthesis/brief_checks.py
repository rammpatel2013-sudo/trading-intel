"""Daily-brief self-audit — seven arithmetic checks between things that must agree.

Born 2026-10-22, the morning the brief printed 8,000 as both walls, a spot 80 pts
stale, and a month-old hallucinated Doc level — none of which anything caught.
Each check compares two independent numbers (or a number and its own date);
results go to the log and to one footer line in the brief. No messages (Mithil's
call: he reads the brief, not an ops channel). Descriptor only (rule 4).
"""
from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trading_intel.memory.models import ResearchNote

log = structlog.get_logger(__name__)

SPOT_GAP_PCT = 0.4      # pre-open SPY-implied vs chain Δ=0.50
FLIP_GAP_PCT = 0.3      # market-read flip vs index-board flip
WALL_BAND_PCT = 3.0     # walls must sit within this of spot, on the right side
STATED_GAP_PCT = 1.0    # letter's stated flip vs ours → warning
MAX_FILTERED = 3        # extractor mis-tags per source before a warning

_SOURCES = {"__DOC__": "DOC", "__VOLSIGNALS__": "VOLSIGNALS", "__KURT__": "KURT", "__NORSEMAN__": "NORSEMAN"}


def _r(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _pct(a: float, b: float) -> float:
    return abs(a - b) / abs(b) * 100.0 if b else 0.0


def run_checks(session: Session | None, ctx: dict[str, Any], *, today: date | None = None) -> list[dict[str, str]]:
    today = today or date.today()
    out: list[dict[str, str]] = []
    spx = next((ix for ix in (ctx.get("indices") or []) if ix.get("symbol") == "SPX"), None) or {}
    doc = ctx.get("doc") or {}
    z = ctx.get("zero_dte") or {}
    spot = _num(spx.get("spot")) or _num(doc.get("spot"))

    # 1. walls sane — right side of spot, within the band; 0DTE walls likewise
    probs = []
    if spot:
        for lab, key, side in (("put wall", "put_wall", -1), ("call wall", "call_wall", 1)):
            w = _num(doc.get(key))
            if w is None:
                probs.append(f"{lab} missing")
            elif (w - spot) * side < 0:
                probs.append(f"{lab} {w:,.0f} on the wrong side of spot {spot:,.0f}")
            elif _pct(w, spot) > WALL_BAND_PCT:
                probs.append(f"{lab} {w:,.0f} is {_pct(w, spot):.1f}% from spot")
        zs = _num(z.get("spot"))
        if zs and z.get("put_wall") and z.get("call_wall") and not (z["put_wall"] <= zs <= z["call_wall"]):
            probs.append(f"0DTE walls {z['put_wall']:,.0f}/{z['call_wall']:,.0f} straddle wrong vs {zs:,.0f}")
    out.append(_r("walls", "fail" if probs else "ok", "; ".join(probs) or f"{_num(doc.get('put_wall')) or 0:,.0f} ≤ {spot or 0:,.0f} ≤ {_num(doc.get('call_wall')) or 0:,.0f}"))

    # 2. spot current — SPY-implied vs chain, or a board that is still yesterday's
    pre = ctx.get("pre_open_spot") or {}
    if pre.get("src") == "spy" and pre.get("chain_spot"):
        gap = _num(pre.get("gap_pct")) or 0.0
        out.append(_r("spot", "fail" if gap > SPOT_GAP_PCT else "ok",
                      f"SPY-implied {pre['spot']:,.0f} vs chain {pre['chain_spot']:,.0f} ({gap:.2f}%)"))
    elif pre.get("spot"):
        out.append(_r("spot", "warn", f"pre-open spot from {pre.get('src')} only ({pre['spot']:,.0f}); no second source"))
    else:
        asof = str(spx.get("asof") or "")
        out.append(_r("spot", "fail" if asof < today.isoformat() else "ok",
                      f"board spot {spot or 0:,.0f} dated {asof or '?'}" + (" — yesterday's close, no pre-open source" if asof < today.isoformat() else "")))

    # 3. flip consistent — market-read mechanics vs index board
    mf = _num(((ctx.get("market_read") or {}).get("mechanics") or {}).get("gex_flip"))
    bf = _num(spx.get("flip"))
    if mf and bf:
        g = _pct(mf, bf)
        out.append(_r("flip", "fail" if g > FLIP_GAP_PCT else "ok", f"market-read {mf:,.0f} vs board {bf:,.0f} ({g:.2f}%)"))
    else:
        out.append(_r("flip", "warn", "one of the two flips is missing"))

    # 4. letters alive — extracted levels dated like the newest stored letter, ≥1 level
    srcs = (ctx.get("newsletter") or {}).get("sources") or {}
    dead = []
    if session is not None:
        for key, label in _SOURCES.items():
            newest = session.execute(select(func.max(ResearchNote.as_of)).where(ResearchNote.symbol == key)).scalar_one_or_none()
            blk = srcs.get(label) or {}
            if newest is None:
                continue
            if str(blk.get("as_of") or "") != newest.isoformat():
                dead.append(f"{label} levels {blk.get('as_of') or 'none'} vs letter {newest.isoformat()}")
            elif not (blk.get("levels") or blk.get("scenarios")):
                dead.append(f"{label} parsed nothing")
    out.append(_r("letters", "fail" if dead else "ok", "; ".join(dead) or f"{len(srcs)} sources current"))

    # 5. levels plausible — how much the sanitiser had to drop
    filt = ctx.get("filtered_levels") or []
    by_src: dict[str, int] = {}
    for f in filt:
        by_src[f["source"]] = by_src.get(f["source"], 0) + 1
    noisy = [f"{k} ×{v}" for k, v in by_src.items() if v > MAX_FILTERED]
    out.append(_r("levels", "warn" if noisy else "ok", ("extractor noisy: " + ", ".join(noisy)) if noisy else f"{len(filt)} mis-tag(s) filtered"))

    # 6. data fresh — chain today, board and VIX from the last session
    sd = str(ctx.get("session_date") or "")
    stale = []
    if not z:
        stale.append("no 0DTE chain for today")
    if str(spx.get("asof") or "") < sd:
        stale.append(f"SPX board {spx.get('asof')}")
    va = str((ctx.get("vix") or {}).get("asof") or "")
    if va and va < sd:
        stale.append(f"VIX {va}")
    out.append(_r("fresh", "fail" if stale else "ok", "; ".join(stale) or f"chain today, board {spx.get('asof')}, VIX {va or '?'}"))

    # 7. stated vs ours — a letter's flip far from ours is a disagreement worth seeing, not a bug
    warns = []
    ours = bf or mf
    if ours:
        for label in ("DOC", "KURT"):
            blk = srcs.get(label) or {}
            if str(blk.get("as_of") or "") != today.isoformat():
                continue
            for lv in blk.get("levels") or []:
                if (lv.get("name") or "").lower() in ("gamma_flip", "flip", "zero_gamma"):
                    sv = _num(lv.get("value"))
                    if sv and _pct(sv, ours) > STATED_GAP_PCT:
                        warns.append(f"{label} flip {sv:,.0f} vs ours {ours:,.0f}")
    out.append(_r("stated", "warn" if warns else "ok", "; ".join(warns) or "no fresh letter disagrees on the flip"))

    for r in out:
        log.info("brief.check", name=r["name"], status=r["status"], detail=r["detail"])
    n_ok = sum(1 for r in out if r["status"] == "ok")
    log.info("brief.checks", passed=n_ok, total=len(out),
             failed=[r["name"] for r in out if r["status"] == "fail"], warned=[r["name"] for r in out if r["status"] == "warn"])
    return out


def summary_line(results: list[dict[str, str]]) -> str:
    if not results:
        return ""
    n_ok = sum(1 for r in results if r["status"] == "ok")
    bad = [r for r in results if r["status"] != "ok"]
    if not bad:
        return f"data checks {n_ok}/{len(results)} pass"
    return f"data checks {n_ok}/{len(results)} — " + " · ".join(f"{r['status'].upper()} {r['name']}: {r['detail']}" for r in bad)
