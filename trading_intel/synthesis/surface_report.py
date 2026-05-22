"""Interpretation / read-through for a volatility surface.

Two layers:
- ``surface_metrics`` — deterministic structured metrics (ATM, 25-delta skew,
  5-delta wings, term-structure slope, forward vol). Pure, testable.
- ``interpret_surface`` — a deterministic markdown read-through grounded in the
  desk frameworks. ``interpret_surface_llm`` adds a richer Ollama narrative using
  the ingested methodology playbooks as context ("based on knowledge we gained").

Regime descriptor / educational read-through only — no trade signals
(FlashAlpha rule 4).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from trading_intel.greeks.surface import DeltaSurface, forward_vol
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import SURFACE_INTERPRETATION_PROMPT

# Playbooks whose frameworks are most relevant to surface interpretation.
_KB_FILES = (
    "trading-volatility.md",
    "managingsmilerisk.md",
    "santander-volatility-trading-primer-part-i-1.md",
)


def _idx(deltas: np.ndarray, target: float) -> int:
    return int(np.argmin(np.abs(deltas - target)))


def surface_metrics(surface: DeltaSurface) -> dict:
    """Deterministic surface metrics (decimals; skew/slope as IV differences)."""
    d = surface.deltas
    i5, i25 = _idx(d, 5), _idx(d, 25)
    atm = surface.atm_iv
    fwd = forward_vol(surface.dte, atm)
    per_expiry = []
    for j in range(surface.n_expiries):
        fv = fwd[j]
        per_expiry.append(
            {
                "expiry": surface.expiries[j].isoformat(),
                "dte": int(surface.dte[j]),
                "atm": round(float(atm[j]), 4),
                "skew_25d": round(float(surface.iv_put[j, i25] - surface.iv_call[j, i25]), 4),
                "put_wing_5d": round(float(surface.iv_put[j, i5] - atm[j]), 4),
                "call_wing_5d": round(float(surface.iv_call[j, i5] - atm[j]), 4),
                "rr_5d": round(float(surface.iv_put[j, i5] - surface.iv_call[j, i5]), 4),
                "forward_vol": (round(float(fv), 4) if np.isfinite(fv) else None),
            }
        )
    term_slope = float(atm[-1] - atm[0]) if surface.n_expiries > 1 else 0.0
    return {
        "spot": (round(surface.spot, 2) if np.isfinite(surface.spot) else None),
        "ref": surface.ref.isoformat(),
        "term_slope": round(term_slope, 4),
        "per_expiry": per_expiry,
    }


def _skew_label(skew_pts: float) -> str:
    if skew_pts >= 3:
        return "steep (strong downside / crash-protection demand)"
    if skew_pts >= 1:
        return "moderate downside skew"
    if skew_pts <= -1:
        return "inverted (call skew / upside demand)"
    return "flat"


def _term_label(slope_pts: float) -> str:
    if slope_pts >= 0.5:
        return "upward / contango (longer-dated richer)"
    if slope_pts <= -0.5:
        return "inverted / backwardation (front richer - near-term stress)"
    return "flat"


def interpret_surface(metrics: dict) -> str:
    """Deterministic markdown read-through grounded in the desk frameworks."""
    per = metrics["per_expiry"]
    front = per[0]
    skew_pts = front["skew_25d"] * 100
    term_pts = metrics["term_slope"] * 100
    lines = [
        "## Surface read",
        (
            f"Front expiry {front['expiry']} ({front['dte']}d): ATM IV "
            f"{front['atm'] * 100:.1f}%. 25-delta skew {skew_pts:+.1f} pts - "
            f"{_skew_label(skew_pts)}. 5-delta put wing {front['put_wing_5d'] * 100:+.1f} "
            f"pts over ATM."
        ),
        (
            f"Term structure: front {per[0]['atm'] * 100:.1f}% -> back "
            f"{per[-1]['atm'] * 100:.1f}% ({term_pts:+.1f} pts) - {_term_label(term_pts)}."
        ),
    ]
    rr_front = per[0]["rr_5d"] * 100
    rr_back = per[-1]["rr_5d"] * 100
    if abs(rr_back) < abs(rr_front):
        rr_shape = "skew compresses with tenor (front-loaded protection demand)"
    else:
        rr_shape = "skew steepens with tenor"
    lines.append(
        f"5% risk reversal: {rr_front:+.1f} pts front -> {rr_back:+.1f} pts back - {rr_shape}."
    )
    fv = [p["forward_vol"] for p in per if p["forward_vol"] is not None]
    if len(fv) > 1:
        steps = " -> ".join(f"{v * 100:.1f}%" for v in fv)
        lines.append(f"Forward vol: {steps} (forward vol exceeds spot vol where the term rises).")
    lines += [
        "",
        "## What it implies (per desk methodology)",
        (
            "- Downside (put) skew reflects hedger/dealer demand for crash protection; a "
            "steep front-month left wing means protection is being bid."
        ),
        (
            "- Term-structure shape sets the carry: contango favours roll-down on longer-dated "
            "vol; backwardation flags near-term event / stress pricing."
        ),
        (
            "- Track fixed-strike vs ATM vol over coming sessions to read the sticky regime "
            "(sticky-strike vs sticky-delta) - that needs the daily snapshot history."
        ),
    ]
    return "\n".join(lines)


def load_kb_context(playbook_dir: str | Path = "docs/playbooks", *, max_chars: int = 6000) -> str:
    """Concatenate relevant methodology playbooks as LLM grounding context."""
    base = Path(playbook_dir)
    parts: list[str] = []
    for name in _KB_FILES:
        path = base / name
        if path.exists():
            parts.append(f"### {name}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)[:max_chars]


def interpret_surface_llm(
    metrics: dict, llm: LLMProvider, *, kb_text: str = "", model: str | None = None
) -> str:
    """Richer narrative via the LLM, grounded in the methodology playbooks."""
    prompt = SURFACE_INTERPRETATION_PROMPT.format(
        metrics=json.dumps(metrics, indent=2), kb=kb_text or "(none provided)"
    )
    return llm.complete(prompt, model=model, max_tokens=600).strip()
