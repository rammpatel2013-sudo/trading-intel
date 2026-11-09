"""Confluence read-out — one input bundle → the full read-out dict (pure, no DB).

``ConfluenceInputs`` holds everything the five layers need for ONE session. The DB
reader (``trading_intel.api.confluence``) fills it from banked tables; tests and the
offline fixture path fill it from JSON (``inputs_from_json``). ``compute_readout``
runs every layer and the merge, so both paths share one code route.

Positioning read, not a trade signal (CLAUDE.md rule 4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date
from typing import Any

from trading_intel.market import confluence as cf
from trading_intel.market.confluence_greeks import GreekRow, greek_levels
from trading_intel.market.confluence_layers import (
    DEFAULT_BETA_PTS,
    Bar,
    BigTradeLeg,
    ChainRow,
    SmilePoint,
    VixOption,
    crowd_levels,
    measured_beta_pts,
    oi_levels,
    price_levels,
    vix_levels,
)

GREEK_DTE_MAX = 45


@dataclass(slots=True)
class ConfluenceInputs:
    as_of: date
    spot: float
    chain: list[ChainRow] = field(default_factory=list)
    vix_spot: float | None = None
    vix_options: list[VixOption] = field(default_factory=list)
    vix_history: list[tuple[date, float]] = field(default_factory=list)
    spx_bars: list[Bar] = field(default_factory=list)
    spy_bars: list[Bar] = field(default_factory=list)
    smile: list[SmilePoint] = field(default_factory=list)
    trades: list[BigTradeLeg] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # source caveats from the loader


def compute_readout(
    inp: ConfluenceInputs, *, window: float = cf.DEFAULT_WINDOW, prior: dict | None = None,
    beta_pts: float = DEFAULT_BETA_PTS,
) -> dict[str, Any]:
    """Run all five layers + the merge for one session."""
    gaps: list[str] = list(inp.notes)
    rows = [r for r in inp.chain if 0 <= r.dte <= GREEK_DTE_MAX]
    if rows:
        greeks = greek_levels([GreekRow(r.strike, r.cp, r.dte, r.iv, r.oi) for r in rows], inp.spot)
        if all(not r.iv for r in rows):
            gaps.append("chain has no IV column: greek layer empty")
    else:
        greeks = []
        gaps.append("no per-strike chain (0-45 DTE) for the day: greek layer skipped")
    oi, g = oi_levels(rows, spot=inp.spot)
    gaps += g
    spx_close = [(b.d, b.close) for b in inp.spx_bars if b.d <= inp.as_of]
    beta_meas = measured_beta_pts(spx_close, inp.vix_history)
    vix, g = vix_levels(inp.vix_options, as_of=inp.as_of, vix_spot=inp.vix_spot, spot=inp.spot,
                        beta_pts=beta_pts, beta_measured=beta_meas)
    gaps += g
    price, g = price_levels(inp.spx_bars, as_of=inp.as_of, spot=inp.spot, spy=inp.spy_bars)
    gaps += g
    crowd, g = crowd_levels(inp.smile, inp.trades, as_of=inp.as_of, spot=inp.spot)
    gaps += g
    extra = {"beta_pts_default": beta_pts, "beta_pts_measured": beta_meas,
             "vix_spot": inp.vix_spot, "n_contracts": len(rows)}
    return cf.build_readout(
        {"greeks": greeks, "oi": oi, "vix": vix, "price": price, "crowd": crowd},
        spot=inp.spot, as_of=inp.as_of.isoformat(), window=window, gaps=gaps, prior=prior,
        extra=extra,
    )


# ── JSON round-trip (fixtures / offline runs) ────────────────────────────────
_LISTS = {"chain": ChainRow, "vix_options": VixOption, "spx_bars": Bar, "spy_bars": Bar,
          "smile": SmilePoint, "trades": BigTradeLeg}
_DATE_FIELDS = {"expiry", "expiration", "d", "near_expiry", "trade_date"}


def _enc(v: object) -> object:
    return v.isoformat() if isinstance(v, date) else v


def inputs_to_json(inp: ConfluenceInputs) -> dict[str, Any]:
    out: dict[str, Any] = {"as_of": inp.as_of.isoformat(), "spot": inp.spot,
                           "vix_spot": inp.vix_spot, "notes": list(inp.notes),
                           "vix_history": [[d.isoformat(), v] for d, v in inp.vix_history]}
    for name in _LISTS:
        out[name] = [{k: _enc(v) for k, v in asdict(x).items()} for x in getattr(inp, name)]
    return out


def _dec(cls: type, d: dict) -> object:
    names = {f.name for f in fields(cls)}
    kw = {k: (date.fromisoformat(v) if k in _DATE_FIELDS and isinstance(v, str) else v)
          for k, v in d.items() if k in names}
    return cls(**kw)


def inputs_from_json(d: dict[str, Any]) -> ConfluenceInputs:
    inp = ConfluenceInputs(as_of=date.fromisoformat(d["as_of"]), spot=float(d["spot"]),
                           vix_spot=d.get("vix_spot"), notes=list(d.get("notes", [])),
                           vix_history=[(date.fromisoformat(a), float(b))
                                        for a, b in d.get("vix_history", [])])
    for name, cls in _LISTS.items():
        setattr(inp, name, [_dec(cls, x) for x in d.get(name, [])])
    return inp


# ── cockpit payload (scripts/cvxmap/data/confluence.json) ────────────────────
COCKPIT_BARS = 60
COCKPIT_BAND = 0.08  # zones further than 8% from spot are left out of the cockpit file


def _slim_zone(z: dict) -> dict:
    keep = ("centre", "low", "high", "layers", "n_layers", "key", "oi_change", "distance",
            "roc", "levels", "info_levels")
    return {k: z.get(k) for k in keep}


def cockpit_payload(readout: dict, *, prior: dict | None = None, score: dict | None = None,
                    bars: list[Bar] | None = None, generated: str | None = None) -> dict:
    """Everything the cockpit Levels tab draws, in one small JSON.

    Latest read-out (zones within 8% of spot), the prior day's zone centres (for the
    "where it sat yesterday" ghosts), a score summary and the last 60 SPX daily bars.
    """
    from trading_intel.market.confluence_score import headline

    spot = float(readout["spot"])
    near = [z for z in readout.get("zones", [])
            if abs(float(z["centre"]) - spot) <= spot * COCKPIT_BAND]
    pz = []
    if prior:
        pz = [{k: z.get(k) for k in ("centre", "low", "high", "n_layers", "key")}
              for z in prior.get("zones", []) if z.get("n_layers", 0) >= 2]
    sc = None
    if score:
        hz = score.get("horizons", {}).get("5") or score.get("horizons", {}).get("1") or {}
        sc = {"headline": headline(score), "horizon": 5 if "5" in score.get("horizons", {}) else 1,
              "key": hz.get("key"), "single": hz.get("single"), "multi2": hz.get("multi2"),
              "days": score.get("n_days_stored"), "min_sample": score.get("min_sample")}
    bb = sorted(bars or [], key=lambda x: x.d)[-COCKPIT_BARS:]
    return {
        "found": True, "symbol": readout.get("symbol", "SPX"), "asof": readout["as_of"],
        "generated": generated, "spot": spot, "window": readout.get("window"),
        "key_min": readout.get("key_min_layers", 3), "disclaimer": readout.get("disclaimer"),
        "info_note": readout.get("info_note"), "vix_spot": readout.get("vix_spot"),
        "beta_default": readout.get("beta_pts_default"),
        "beta_measured": readout.get("beta_pts_measured"),
        "zones": [_slim_zone(z) for z in near], "info_levels": readout.get("info_levels", []),
        "prior_asof": prior.get("as_of") if prior else None,
        "prior_spot": (prior or {}).get("spot"),
        "prior_zones": pz, "score": sc, "gaps": readout.get("gaps", []),
        "bars": [{"d": x.d.isoformat(), "o": round(x.open, 2), "h": round(x.high, 2),
                  "l": round(x.low, 2), "c": round(x.close, 2)} for x in bb],
    }
