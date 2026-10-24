"""Sector lead/lag + fragility/stability scan — the brain of the sector report.

Pure transforms (assembled descriptors in → ranking + flags out): no vendor/DB
dependency, so every function here is unit-testable. Descriptor only (FlashAlpha
rule 4): this emits lead/lag ranks, a stability↔fragility read, and LEAP-setup
FLAGS with rationale — never a trade signal. The actual LEAP selection stays in
the validated ``strategies/`` layer (rule 4); this is context, not a green light.

The thesis (from the dealer / vol knowledge base):
  * Long gamma  → dealers dampen  → STABLE tape (a wall that tends to hold).
  * Short gamma → dealers amplify → FRAGILE tape (a wall that tends to break).
  * Above the gamma flip = cushion under spot; below = air pocket.
  * Low ATM IV / low IV percentile = cheap vega = a better LEAP-long entry.
  * Low sector correlation (dispersion regime) = single-sector bets actually
    diversify and pay; high correlation = everything is index beta wearing a
    sector costume — that's the go / no-go GATE on the whole lead/lag idea.
  * LEAD  = stable + cheap vega + firm relative momentum + above the flip.
  * LAG   = the mirror: fragile + rich vega + weak momentum + below the flip.

Weights below are a documented PROPOSAL (like the correlation thresholds in
``sector_correlation``) — tune against outcomes; they are not sacred.
"""
from __future__ import annotations

import math
from statistics import mean, pstdev

# Composite lead/lag weights (sum≈1). Stability + cushion describe the dealer
# floor; momentum is the tape; cheap vega is the entry edge for a LONG option.
_W_STABILITY = 0.30
_W_CUSHION = 0.20
_W_MOMENTUM = 0.30
_W_IV_CHEAP = 0.20

# LEAP-flag thresholds (proposal).
_IV_CHEAP_PCTILE = 0.35
_IV_RICH_PCTILE = 0.70


def _fin(x) -> float | None:
    """Coerce to a finite float or None."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _zmap(vals: list[float | None]) -> list[float | None]:
    """Cross-sectional z-scores, None-safe (missing → None, contributes 0 later)."""
    present = [v for v in vals if v is not None]
    if len(present) < 2:
        return [0.0 if v is not None else None for v in vals]
    mu = mean(present)
    sd = pstdev(present)
    if sd == 0:
        return [0.0 if v is not None else None for v in vals]
    return [((v - mu) / sd) if v is not None else None for v in vals]


def classify_sector(row: dict) -> dict:
    """Per-SPDR fragility/stability descriptors from one assembled row.

    ``row`` keys (all optional/None-safe): symbol, spot, gex_total, dex_total,
    gex_flip, dex_flip, atm_iv, iv_pctile, ret_21d, ret_63d, rr25.
    """
    gex = _fin(row.get("gex_total"))
    spot = _fin(row.get("spot"))
    gflip = _fin(row.get("gex_flip"))
    dex = _fin(row.get("dex_total"))
    dflip = _fin(row.get("dex_flip"))

    gamma_regime = None if gex is None else ("long" if gex >= 0 else "short")
    stability = None if gamma_regime is None else ("stable" if gamma_regime == "long" else "fragile")
    gflip_cushion = ((spot - gflip) / spot) if (spot and gflip) else None
    dflip_cushion = ((spot - dflip) / spot) if (spot and dflip) else None
    delta_lean = None if dex is None else ("long" if dex > 0 else "short" if dex < 0 else "flat")

    return {
        "symbol": row.get("symbol"),
        "spot": spot,
        "gamma_regime": gamma_regime,
        "stability": stability,
        "gex_total": gex,
        "dex_total": dex,
        "delta_lean": delta_lean,
        "gflip": gflip,
        "gflip_cushion": gflip_cushion,
        "dflip": dflip,
        "dflip_cushion": dflip_cushion,
        "atm_iv": _fin(row.get("atm_iv")),
        "iv_pctile": _fin(row.get("iv_pctile")),
        "ret_21d": _fin(row.get("ret_21d")),
        "ret_63d": _fin(row.get("ret_63d")),
        "rr25": _fin(row.get("rr25")),  # optional skew (Layer-2); None until collected
        "rr25_dte": row.get("rr25_dte"),
        "call_wall": _fin(row.get("call_wall")),
        "put_wall": _fin(row.get("put_wall")),
        "footprint": row.get("footprint"),  # fixed-strike offered/bid read (Layer-2)
        "rr25_shift": _fin(row.get("rr25_shift")),  # day-over-day 25Δ RR change (put↔call rotation)
        "rr25_trend": row.get("rr25_trend"),
    }


def lead_lag_rank(classified: list[dict]) -> list[dict]:
    """Rank sectors leader→laggard by a composite z-scored lead score.

    score = 0.30·stability + 0.20·flip-cushion_z + 0.30·momentum_z + 0.20·(−IV_z)

    stability is +1 long-gamma / −1 short-gamma (0 if unknown). IV cheapness uses
    the IV percentile when present, else the cross-sectional z of ATM IV (negated:
    cheaper = higher score). Everything is None-safe: a missing component drops out
    and its weight is renormalised across the components that ARE present, so a
    sector is never penalised merely for having a gap.
    """
    n = len(classified)
    if n == 0:
        return []

    cushion_z = _zmap([c.get("gflip_cushion") for c in classified])
    mom_z = _zmap([c.get("ret_21d") for c in classified])
    # IV cheapness: prefer percentile (0..1 → centered), else z of ATM IV.
    iv_pct = [c.get("iv_pctile") for c in classified]
    if any(v is not None for v in iv_pct):
        iv_cheap = [(0.5 - v) * 2.0 if v is not None else None for v in iv_pct]  # low pctile → +1
    else:
        iv_z = _zmap([c.get("atm_iv") for c in classified])
        iv_cheap = [(-v) if v is not None else None for v in iv_z]

    out = []
    for i, c in enumerate(classified):
        stab = None
        if c.get("gamma_regime") == "long":
            stab = 1.0
        elif c.get("gamma_regime") == "short":
            stab = -1.0
        parts = [
            (_W_STABILITY, stab),
            (_W_CUSHION, cushion_z[i]),
            (_W_MOMENTUM, mom_z[i]),
            (_W_IV_CHEAP, iv_cheap[i]),
        ]
        wsum = sum(w for w, v in parts if v is not None)
        score = (sum(w * v for w, v in parts if v is not None) / wsum) if wsum else None
        out.append({**c, "lead_score": score})

    # Rank: known scores descending; unknown scores sink to the bottom, stable order.
    ranked = sorted(
        out,
        key=lambda d: (d["lead_score"] is None, -(d["lead_score"] or 0.0), d.get("symbol") or ""),
    )
    for pos, d in enumerate(ranked, start=1):
        d["rank"] = pos
    return ranked


def leap_flags(cls: dict, *, corr_regime: str | None) -> dict:
    """Descriptor LEAP-setup FLAGS for one sector (rule 4 — flags, not signals).

    Returns ``setup`` in {candidate, watch, avoid, n/a} with the ``for`` / ``against``
    rationale lists and whether the correlation ``dispersion_gate`` is open.
    """
    for_: list[str] = []
    against: list[str] = []

    stab = cls.get("stability")
    if stab == "stable":
        for_.append("long gamma — stable tape (wall tends to hold)")
    elif stab == "fragile":
        against.append("short gamma — fragile tape (wall tends to break)")

    gc = cls.get("gflip_cushion")
    if gc is not None and gc > 0:
        for_.append("above the gamma flip (cushion under spot)")
    elif gc is not None and gc < 0:
        against.append("below the gamma flip (air pocket)")

    ivp = cls.get("iv_pctile")
    if ivp is not None and ivp < _IV_CHEAP_PCTILE:
        for_.append(f"cheap vega — ATM IV in the {round(ivp * 100)}th pctile")
    elif ivp is not None and ivp > _IV_RICH_PCTILE:
        against.append(f"rich vega — ATM IV in the {round(ivp * 100)}th pctile")

    mom = cls.get("ret_21d")
    if mom is not None and mom > 0:
        for_.append("positive 21-day relative momentum")
    elif mom is not None and mom < 0:
        against.append("negative 21-day momentum")

    # Optional skew (Layer-2): put-rich skew on a stable name = a cheaper call side.
    rr = cls.get("rr25")
    if rr is not None and rr > 0 and stab == "stable":
        for_.append("put-rich skew — call side relatively cheap")

    # Fixed-strike footprint (Layer-2): near-money vol offered = levels holding.
    fp = cls.get("footprint") or {}
    if not fp.get("pending") and fp.get("read"):
        if "HOLD" in fp["read"]:
            for_.append("fixed-strike vol offered — nearby levels holding")
        elif "BREAK" in fp["read"]:
            against.append("fixed-strike vol bid — nearby levels fragile")

    # Skew SHIFT (Layer-2): rr25 falling = demand rotating to the call side = the
    # bullish LEAP-call tell; rising = defensive rotation to puts.
    shift = cls.get("rr25_shift")
    if shift is not None and shift < -0.005 and stab == "stable":
        for_.append("skew rotating to the call side (bullish demand shift)")
    elif shift is not None and shift > 0.01:
        against.append("skew rotating to the put side (defensive)")

    dispersion_gate = bool(corr_regime and corr_regime.startswith("low"))

    if stab is None:
        setup = "n/a"
    elif stab == "fragile" and len(against) >= 2:
        setup = "avoid"
    elif stab == "stable" and len(for_) >= 2 and dispersion_gate:
        setup = "candidate"
    else:
        setup = "watch"

    return {"setup": setup, "for": for_, "against": against, "dispersion_gate": dispersion_gate}


def internals_health(spdr_dir: dict, index_dir: float | None) -> dict:
    """Market-internals read: how many SPDRs are up vs the index's own direction.

    ``spdr_dir`` maps symbol → today's return (float); ``index_dir`` is SPY's
    return. Surfaces the divergence the desk cares about: breadth-vs-index (most
    sectors up while the index is down = broad rotation under a heavy-cap drag;
    most down while the index is up = narrow, fragile leadership).
    """
    ups = [s for s, r in spdr_dir.items() if r is not None and r > 0]
    downs = [s for s, r in spdr_dir.items() if r is not None and r < 0]
    n = len([r for r in spdr_dir.values() if r is not None])
    pct_up = (len(ups) / n) if n else None
    idx_up = None if index_dir is None else index_dir > 0

    divergence = None
    if pct_up is not None and idx_up is not None:
        if pct_up >= 0.6 and not idx_up:
            divergence = "broad-up / index-down — rotation under a heavy-cap drag"
        elif pct_up <= 0.4 and idx_up:
            divergence = "narrow — index up on few sectors (fragile leadership)"
        else:
            divergence = "aligned — breadth agrees with the index"

    healthy = None
    if pct_up is not None:
        healthy = pct_up >= 0.5 and (idx_up is not False or pct_up >= 0.6)

    return {
        "n": n,
        "n_up": len(ups),
        "n_down": len(downs),
        "pct_up": pct_up,
        "index_up": idx_up,
        "divergence": divergence,
        "healthy": healthy,
    }


def build_sector_scan(rows: list[dict], *, corr: dict | None, internals: dict | None,
                      gex_history: dict | None = None) -> dict:
    """Assemble the full sector scan: per-SPDR classification, lead/lag ranking,
    LEAP flags, the correlation gate, and the internals read.

    ``corr`` is a ``sector_correlation.latest_snapshot``-shaped dict (or DB row
    projected to it). ``internals`` is an ``internals_health`` result.
    """
    corr = corr or {}
    # Longest-window regime string drives the dispersion gate.
    regime_map = corr.get("regime") or {}
    regime_str = None
    for key in ("63d", "21d"):
        if regime_map.get(key):
            regime_str = regime_map[key]
            break

    classified = [classify_sector(r) for r in rows]
    ranked = lead_lag_rank(classified)
    for d in ranked:
        d["leap"] = leap_flags(d, corr_regime=regime_str)

    scored = [d for d in ranked if d.get("lead_score") is not None]
    # up to 3 each, but never overlapping when the priced set is small
    k = min(3, len(scored) // 2)
    leaders = scored[:k]
    laggards = list(reversed(scored[-k:])) if k else []

    candidates = [d["symbol"] for d in ranked if d["leap"]["setup"] == "candidate"]
    avoid = [d["symbol"] for d in ranked if d["leap"]["setup"] == "avoid"]

    return {
        "sectors": ranked,
        "gamma_split": gamma_split(classified, gex_history=gex_history),
        "leaders": [d["symbol"] for d in leaders],
        "laggards": [d["symbol"] for d in laggards],
        "leap_candidates": candidates,
        "leap_avoid": avoid,
        "correlation": {
            "avg_corr": corr.get("avg_corr"),
            "regime": regime_map,
            "dispersion": corr.get("dispersion"),
            "as_of": corr.get("as_of"),
            "gate_open": bool(regime_str and regime_str.startswith("low")),
            "regime_label": regime_str,
        },
        "internals": internals or {},
        "n_sectors": len(rows),
    }


# ── Gamma split: cyclicals vs defensives (added 2026-10-22) ──────────────────
CYCLICAL = ("XLK", "XLY", "XLI", "XLF", "XLB", "XLC", "XLE")
DEFENSIVE = ("XLU", "XLP", "XLV", "XLRE")


def _bucket(classified: list[dict], members: tuple[str, ...], hist: dict | None) -> dict:
    rows = [c for c in classified if c.get("symbol") in members and c.get("gex_total") is not None]
    gex = sum(c["gex_total"] for c in rows)
    above = sum(1 for c in rows if (c.get("gflip_cushion") or 0) > 0)
    below = sum(1 for c in rows if c.get("gflip_cushion") is not None and c["gflip_cushion"] <= 0)
    cush = sorted(c["gflip_cushion"] for c in rows if c.get("gflip_cushion") is not None)
    med = cush[len(cush) // 2] if cush else None
    short = sorted((c["symbol"] for c in rows if c["gex_total"] < 0), key=lambda s: s)
    long_ = sorted((c["symbol"] for c in rows if c["gex_total"] >= 0), key=lambda s: s)
    # bucket GEX history = sum across members per day (only days every priced member has)
    series: list[float] = []
    if hist:
        by_day: dict = {}
        for sym in members:
            for ts, g in (hist.get(sym) or []):
                by_day.setdefault(ts, {})[sym] = g
        priced = {c["symbol"] for c in rows}
        for ts in sorted(by_day):
            d = by_day[ts]
            if priced and priced.issubset(d.keys()):
                series.append(sum(d[s] for s in priced))
    return {
        "n": len(rows),
        "gex_total": gex if rows else None,
        "sign": None if not rows else ("short" if gex < 0 else "long"),
        "n_above_flip": above,
        "n_below_flip": below,
        "median_cushion": med,
        "short_gamma": short,
        "long_gamma": long_,
        "history": series[-10:],
    }


def gamma_split(classified: list[dict], *, gex_history: dict | None = None) -> dict:
    """Cyclicals-vs-defensives dealer-gamma split (descriptor, rule 4).

    ``gex_history`` = {symbol: [(ts, gex_total), ...] ascending} for sparklines
    (optional). Read: cyclicals short γ while defensives long γ = the index is
    fragile exactly where its weight is (dealers amplify tech/financials/industrials
    and dampen the stuff that doesn't move the index); the mirror = pinned index
    with the cyclicals providing the cushion; both short = broad short gamma;
    both long = broad pin.
    """
    cyc = _bucket(classified, CYCLICAL, gex_history)
    dfn = _bucket(classified, DEFENSIVE, gex_history)
    cs, ds = cyc["sign"], dfn["sign"]
    if cs is None and ds is None:
        label, read = "pending", "awaiting CVForge sector snapshot"
    elif cs == "short" and ds == "long":
        label, read = "fragile-index", "cyclicals SHORT γ / defensives LONG γ — dealers amplify where the index weight is and dampen where it isn't: the index is fragile, the hedges are in the wrong place"
    elif cs == "long" and ds == "short":
        label, read = "cushioned-index", "cyclicals LONG γ / defensives SHORT γ — the weight of the index is pinned; rotation lives in the defensives"
    elif cs == "short" and ds == "short":
        label, read = "broad-short", "both buckets SHORT γ — dealers amplify everything; expect range expansion either way"
    elif cs == "long" and ds == "long":
        label, read = "broad-pin", "both buckets LONG γ — dealers dampen everything; mean-reversion tape"
    else:
        label, read = "mixed", "one bucket unpriced — partial read"
    return {"cyclical": cyc, "defensive": dfn, "label": label, "read": read,
            "members": {"cyclical": list(CYCLICAL), "defensive": list(DEFENSIVE)}}
