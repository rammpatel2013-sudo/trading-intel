"""SPX confluence read-out — merge five level layers into zones (pure, no DB).

Layers (each built in ``confluence_greeks`` / ``confluence_layers``):

    greeks  — vomma peaks, speed extremes, naive dealer gamma zero (per expiry bucket)
    oi      — biggest put-OI / call-OI strikes (<=45d and <=7d) + day-over-day OI change
    vix     — biggest VIX option OI strikes mapped to SPX via spot-vol beta
    price   — SMAs, swing pivots, prior-month high/low, anchored VWAP
    crowd   — 22Δ/25Δ strikes from the smile + strikes of the largest index structures

A **zone** is a cluster of candidate levels spanning at most ``window`` SPX points
(greedy: the window holding the most distinct layers is taken first). Centre =
strength-weighted mean; ``n_layers`` = distinct layers present;
"key" = 3 or more layers. Rate of change compares each zone with the nearest zone in
yesterday's output.

This is a positioning read, not a trade signal (CLAUDE.md rule 4): a zone says where
several independent descriptions of the book and of price history agree, never which
way price goes or whether it holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

LAYERS: tuple[str, ...] = ("greeks", "oi", "vix", "price", "crowd")
LAYER_NAMES: dict[str, str] = {
    "greeks": "Greek peaks", "oi": "Open-interest clusters", "vix": "VIX-mapped levels",
    "price": "Price memory", "crowd": "Crowded strikes",
}
KEY_MIN_LAYERS = 3
DEFAULT_WINDOW = 25.0
DISCLAIMER = "Positioning read, not a trade signal."
# per-level detail fields carried into each zone (tooltips / tables)
DETAIL_KEYS = ("bucket", "oi", "oi_change", "vix_strike", "level_measured_beta", "iv", "premium")
INFO_NOTE = ("Vol-shock peak (info): the strike where ONE option's vomma is largest at today's "
             "spot, IV and time. It depends only on spot, vol and time, not on anyone's "
             "position, so it is shown for reference and never counts toward a zone's layers "
             "or the key (3+ layers) rule.")


@dataclass(frozen=True, slots=True)
class Level:
    layer: str
    level: float
    label: str
    strength: float = 1.0
    kind: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    info: bool = False  # info-only: shown, never counted toward layers / key


@dataclass(slots=True)
class Zone:
    centre: float
    low: float
    high: float
    layers: list[str]
    levels: list[Level]
    strength: float
    oi_change: float | None = None
    distance: float | None = None
    roc: dict[str, Any] | None = None
    info: list[Level] = field(default_factory=list)  # info-only levels near the zone

    @property
    def n_layers(self) -> int:
        return len(self.layers)

    @property
    def key(self) -> bool:
        return self.n_layers >= KEY_MIN_LAYERS


def to_levels(layer: str, items: Iterable[dict]) -> list[Level]:
    """Wrap a layer's plain-dict candidates as ``Level`` objects."""
    out = []
    for d in items:
        meta = {k: v for k, v in d.items()
                if k not in ("level", "label", "strength", "kind", "info")}
        out.append(Level(layer, float(d["level"]), str(d["label"]),
                         float(d.get("strength", 1.0) or 0.0), str(d.get("kind", "")), meta,
                         bool(d.get("info", False))))
    return out


def cluster(levels: Sequence[Level], window: float = DEFAULT_WINDOW) -> list[list[Level]]:
    """Greedy best-window clustering: every cluster spans at most ``window`` points.

    Repeatedly take the window [L, L + window] (L = some remaining level) holding the
    most DISTINCT layers (ties: more total strength, then the lower window), make it a
    cluster, remove its levels, and continue until no level is left. Deterministic.
    """
    rest = sorted(levels, key=lambda x: (x.level, x.layer, x.label))
    out: list[list[Level]] = []
    while rest:
        best: tuple[tuple[int, float, float], int, int] = ((0, 0.0, 0.0), 0, 0)
        found = False
        j = 0
        for i, lo in enumerate(rest):
            j = max(j, i)
            while j + 1 < len(rest) and rest[j + 1].level - lo.level <= window:
                j += 1
            grp = rest[i:j + 1]
            key = (len({x.layer for x in grp}), sum(x.strength for x in grp), -lo.level)
            if not found or key > best[0]:
                best, found = (key, i, j), True
        _, i, j = best
        out.append(rest[i:j + 1])
        rest = rest[:i] + rest[j + 1:]
    return sorted(out, key=lambda g: g[0].level)


def make_zone(group: Sequence[Level], spot: float | None = None) -> Zone:
    w = [max(x.strength, 1e-6) for x in group]
    centre = sum(x.level * wi for x, wi in zip(group, w, strict=True)) / sum(w)
    layers = [lay for lay in LAYERS if any(x.layer == lay for x in group)]
    chg = [x.meta.get("oi_change") for x in group if x.layer == "oi"]
    known = [float(c) for c in chg if c is not None]
    return Zone(
        centre=round(centre, 1), low=round(min(x.level for x in group), 1),
        high=round(max(x.level for x in group), 1), layers=layers, levels=list(group),
        strength=round(sum(w), 3), oi_change=sum(known) if known else None,
        distance=round(centre - spot, 1) if spot is not None else None,
    )


def merge(levels: Sequence[Level], *, spot: float | None = None,
          window: float = DEFAULT_WINDOW) -> list[Zone]:
    """All clusters of SCORED levels as zones, sorted by centre. Single-layer clusters are
    kept (they score as the comparison group); the report highlights multi-layer ones.

    Info-only levels (``info=True``) never enter the clustering, the layer count or the
    key rule; each is attached to the nearest zone whose band (widened by window/2 each
    side) contains it, purely for display.
    """
    zones = [make_zone(g, spot) for g in cluster([x for x in levels if not x.info], window)]
    for x in (x for x in levels if x.info):
        near = [z for z in zones if z.low - window / 2 <= x.level <= z.high + window / 2]
        if near:
            min(near, key=lambda z: abs(z.centre - x.level)).info.append(x)
    return zones


def zone_dict(z: Zone) -> dict[str, Any]:
    return {
        "centre": z.centre, "low": z.low, "high": z.high, "layers": z.layers,
        "n_layers": z.n_layers, "key": z.key, "strength": z.strength,
        "oi_change": z.oi_change, "distance": z.distance, "roc": z.roc,
        "levels": [{"layer": x.layer, "level": x.level, "label": x.label,
                    "strength": x.strength, "kind": x.kind,
                    **{k: x.meta[k] for k in DETAIL_KEYS if x.meta.get(k) is not None}}
                   for x in z.levels],
        "info_levels": [{"layer": x.layer, "level": x.level, "label": x.label,
                         "kind": x.kind} for x in z.info],
    }


def rate_of_change(zones: Sequence[Zone], prior: Sequence[dict] | None, *, spot: float | None,
                   prior_spot: float | None, window: float = DEFAULT_WINDOW) -> None:
    """Attach ``roc`` to each zone: compare with the nearest prior zone within 2·window.

    ``roc = {matched, prior_centre, centre_shift, layer_change, oi_change_delta,
    distance_now, distance_prior}``; ``matched=False`` marks a new zone.
    """
    if prior is None:  # no prior read-out at all: nothing to compare (roc stays None)
        return
    prior = list(prior)
    for z in zones:
        best = min(prior, key=lambda p: abs(float(p["centre"]) - z.centre), default=None)
        if best is None or abs(float(best["centre"]) - z.centre) > 2 * window:
            z.roc = {"matched": False}
            continue
        pc = float(best["centre"])
        oi_now, oi_prev = z.oi_change, best.get("oi_change")
        z.roc = {
            "matched": True, "prior_centre": pc, "centre_shift": round(z.centre - pc, 1),
            "layer_change": z.n_layers - int(best.get("n_layers", 0)),
            "oi_change_delta": (oi_now - float(oi_prev))
            if oi_now is not None and oi_prev is not None else None,
            "distance_now": round(z.centre - spot, 1) if spot is not None else None,
            "distance_prior": round(pc - prior_spot, 1) if prior_spot is not None else None,
        }


def build_readout(
    layer_levels: dict[str, list[dict]], *, spot: float, as_of: str, window: float = DEFAULT_WINDOW,
    gaps: Sequence[str] = (), prior: dict | None = None, extra: dict | None = None,
) -> dict[str, Any]:
    """Assemble the JSON read-out: zones, key zones, raw layer levels, gaps, RoC."""
    levels: list[Level] = []
    for lay in LAYERS:
        levels += to_levels(lay, layer_levels.get(lay, []))
    zones = merge(levels, spot=spot, window=window)
    rate_of_change(zones, (prior or {}).get("zones") if prior else None, spot=spot,
                   prior_spot=(prior or {}).get("spot"), window=window)
    missing = [LAYER_NAMES[lay] for lay in LAYERS
               if not [x for x in layer_levels.get(lay, []) if not x.get("info")]]
    all_gaps = list(gaps) + [f"layer empty: {m}" for m in missing]
    return {
        "symbol": "SPX", "as_of": as_of, "spot": spot, "window": window,
        "key_min_layers": KEY_MIN_LAYERS, "disclaimer": DISCLAIMER,
        "zones": [zone_dict(z) for z in zones],
        "key_zones": [zone_dict(z) for z in zones if z.key],
        "layers": {lay: layer_levels.get(lay, []) for lay in LAYERS},
        "info_levels": [{"layer": x.layer, "level": x.level, "label": x.label, "kind": x.kind}
                        for x in levels if x.info],
        "info_note": INFO_NOTE,
        "gaps": all_gaps, "prior_as_of": (prior or {}).get("as_of"),
        **(extra or {}),
    }
