"""Market synthesis engine — fuse the four pillars into ONE narrative read.

The pure brain (dicts-in → structured read-out; unit-tested). It reconciles:

* REGIME    — Norseman Bull/Bear Line + breadth confirmation ([[breadth]] reader)
* MECHANICS — dealer gamma/delta/flip + expected move ([[positioning]] reader)
* WEATHER   — VIX level + VIX term structure (9d−3m) + vol-of-vol VVIX (get_vix)
* TAPE      — light directional read from the flow/DEX lean

…and encodes the cross-pillar rules (narrow-breadth+short-gamma = fragile;
long-gamma-pin + elevated VVIX = coiled spring; …). Output = path of least
resistance + a levels ladder + if-then triggers (folding in the newsletter
scenarios) + a confluence score + one narrative line. Descriptor only, never a
signal (FlashAlpha rule 4): it describes the interaction of the pillars, it does
NOT emit a trade call. See [[market-synthesis-engine]].
"""
from __future__ import annotations

from typing import Any

_VVIX_ELEVATED = 110.0  # vol-of-vol above this = fragility flag (descriptor threshold)
_BREADTH_WEAK = 40      # % above 200-DMA below this = weak breadth
_BREADTH_STRONG = 70


def _num(x: object) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _regime_pillar(breadth: dict) -> dict:
    above = breadth.get("above_bbl")
    div = breadth.get("divergence") or {}
    state = div.get("state")
    length = div.get("length")
    health = {
        "confirming": "breadth confirming",
        "bearish_div": f"breadth diverging {length or ''}d — top-warning gap".replace("  ", " "),
        "bullish_div": f"breadth firming {length or ''}d".replace("  ", " "),
    }.get(state, "breadth building")
    return {
        "state": "bull-intact" if above else ("bear-prepare" if above is False else "unknown"),
        "above_bbl": above,
        "bull_bear_line": breadth.get("bull_bear_line"),
        "dist_to_bbl": breadth.get("dist_to_bbl"),
        "breadth_health": health.strip(),
        "divergence_state": state,
        "pct_above_200": breadth.get("pct_above_200"),
    }


def _mechanics_pillar(pos: dict) -> dict:
    reg = pos.get("regime") or {}
    amp = reg.get("amplifying")  # True = short gamma (amplify), False = long gamma (pin)
    return {
        "state": "short-gamma (amplify)" if amp else ("long-gamma (pin)" if amp is False else "gamma n/a"),
        "amplifying": amp,
        "gex_flip": reg.get("gex_flip"),
        "dist_to_flip": reg.get("dist_to_flip"),
        "spot": pos.get("spot"),
        "expected_move": pos.get("expected_move") or {},
        "dex_lean": (pos.get("dex") or {}).get("lean"),
    }


def _weather_pillar(vol: dict) -> dict:
    vix = _num(vol.get("vix"))
    vvix = _num(vol.get("vvix"))
    term = _num(vol.get("term_9d_3m"))  # vix9d − vix3m: >0 backwardation (stress), <0 contango (calm)
    if term is None:
        state = "vol n/a"
    elif term > 0:
        state = "stress-backwardation"
    else:
        state = "calm-contango"
    return {
        "state": state,
        "vix": vix,
        "vvix": vvix,
        "term_9d_3m": term,
        "vvix_elevated": bool(vvix is not None and vvix >= _VVIX_ELEVATED),
    }


def _levels_ladder(mech: dict) -> list[dict]:
    spot = _num(mech.get("spot"))
    flip = _num(mech.get("gex_flip"))
    em = mech.get("expected_move") or {}
    lo, hi = _num(em.get("lower")), _num(em.get("upper"))
    rows = [
        {"name": "gamma flip", "value": flip},
        {"name": "EM low", "value": lo},
        {"name": "spot", "value": spot},
        {"name": "EM high", "value": hi},
    ]
    rows = [r for r in rows if r["value"] is not None]
    return sorted(rows, key=lambda r: r["value"])


def _newsletter_triggers(newsletter: dict) -> list[dict]:
    out: list[dict] = []
    for src, blk in (newsletter or {}).get("sources", {}).items():
        for sc in (blk or {}).get("scenarios", []) or []:
            out.append({
                "source": src,
                "trigger": sc.get("trigger"),
                "consequence": sc.get("consequence"),
                "direction": sc.get("direction"),
            })
    return out


def build_read(
    positioning: dict | None,
    breadth: dict | None,
    vol: dict | None,
    newsletter: dict | None = None,
) -> dict[str, Any]:
    """Fuse the pillars into one structured read. Pure; every input degrades to {}."""
    pos = positioning or {}
    reg = _regime_pillar(breadth or {})
    mech = _mechanics_pillar(pos)
    wx = _weather_pillar(vol or {})

    amp = mech["amplifying"]
    wstate = wx["state"]
    div = reg["divergence_state"]
    p200 = reg["pct_above_200"]

    # PATH of least resistance (mechanics × weather)
    if amp is False and wstate == "calm-contango":
        path = "low-energy grind / pin — dealers dampen, vol calm"
    elif amp is True and wstate == "stress-backwardation":
        path = "trend / air pocket — dealers amplify into stressed vol"
    elif amp is False:
        path = "range-bound but with a vol bid underneath — pinned, not calm"
    elif amp is True:
        path = "twitchy / amplified — respect momentum, thin liquidity"
    else:
        path = "mixed — insufficient positioning read"

    # CROSS-PILLAR flags (the non-obvious interactions)
    flags: list[str] = []
    if amp is True and p200 is not None and p200 < _BREADTH_WEAK:
        flags.append("narrow breadth + short gamma = fragility multiplier (small catalyst → outsized move)")
    if amp is False and wx["vvix_elevated"]:
        flags.append("long-gamma pin + elevated VVIX = coiled spring (gap risk when the pin breaks)")
    if wstate == "stress-backwardation" and amp is False:
        flags.append("backwardation but still long-gamma — the pin may hold despite the fear")
    if div == "bearish_div" and amp is False:
        flags.append("breadth rotting under a pin — the classic top setup; watch the Bull/Bear-Line floor")

    # CONFLUENCE — vote the pillars, name the tension when they disagree
    bull = sum([reg["state"] == "bull-intact", amp is False, wstate == "calm-contango", div == "confirming"])
    bear = sum([reg["state"] == "bear-prepare", amp is True, wstate == "stress-backwardation", div == "bearish_div"])
    if bull >= 3 and bear == 0:
        confl = {"score": "aligned-constructive", "aligned": True, "tension": None}
    elif bear >= 3 and bull == 0:
        confl = {"score": "aligned-defensive", "aligned": True, "tension": None}
    else:
        tension = None
        if amp is False and div == "bearish_div":
            tension = "mechanics pin the tape while breadth deteriorates underneath"
        elif amp is True and reg["state"] == "bull-intact":
            tension = "regime still bullish but dealer gamma is amplifying — respect both sides"
        elif wstate == "stress-backwardation" and amp is False:
            tension = "vol stressed but gamma still dampening"
        confl = {"score": "mixed", "aligned": False, "tension": tension or "pillars not unanimous — respect both sides"}

    # TRIGGERS — our thresholds + the newsletter if-then scenarios
    triggers: list[dict] = []
    flip, spot = _num(mech["gex_flip"]), _num(mech["spot"])
    if flip is not None:
        triggers.append({"source": "ours", "trigger": f"close below the gamma flip {flip:.0f}",
                         "consequence": "flips to short gamma → moves amplify", "direction": "bearish"})
    if wx["term_9d_3m"] is not None and wstate == "calm-contango":
        triggers.append({"source": "ours", "trigger": "VIX term flips to backwardation (9d > 3m)",
                         "consequence": "weather turns to stress", "direction": "bearish"})
    if reg["bull_bear_line"] is not None:
        triggers.append({"source": "ours", "trigger": f"weekly close below the Bull/Bear Line {reg['bull_bear_line']:.0f}",
                         "consequence": "Norseman bear-prepare (a 10% close = a test)", "direction": "bearish"})
    triggers.extend(_newsletter_triggers(newsletter))

    # NARRATIVE — the fused one-liner
    vv = " + elevated VVIX (fragile)" if wx["vvix_elevated"] else ""
    narrative = (
        f"{mech['state']} → {path}. Regime {reg['state']}, {reg['breadth_health']}; "
        f"weather {wstate}{vv}. Confluence: {confl['score']}"
        + (f" — {confl['tension']}." if confl["tension"] else ".")
    )

    return {
        "regime": reg,
        "mechanics": mech,
        "weather": wx,
        "path": path,
        "levels": _levels_ladder(mech),
        "triggers": triggers,
        "cross_pillar_flags": flags,
        "confluence": confl,
        "narrative": narrative,
    }
