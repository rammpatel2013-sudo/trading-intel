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
import pandas as pd
import structlog
from sqlalchemy.orm import Session

from trading_intel.greeks.surface import DeltaSurface, forward_vol
from trading_intel.memory.retrieval import format_kb, retrieve_chunks
from trading_intel.strategies.options_flow import (
    FlowSummary,
    Structure,
    format_flow_markdown,
    format_flowsum_markdown,
    format_structures_markdown,
)
from trading_intel.synthesis.llm import LLMProvider
from trading_intel.synthesis.prompts import SURFACE_INTERPRETATION_PROMPT

log = structlog.get_logger(__name__)

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


def _load_kb_from_files(playbook_dir: str | Path, *, max_chars: int) -> str:
    """Fallback: concatenate a few hand-picked methodology playbooks."""
    base = Path(playbook_dir)
    parts: list[str] = []
    for name in _KB_FILES:
        path = base / name
        if path.exists():
            parts.append(f"### {name}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)[:max_chars]


def build_surface_query(metrics: dict) -> str:
    """Compose a retrieval query describing the current surface for the KB search."""
    per = metrics.get("per_expiry") or []
    front = per[0] if per else {}
    return (
        "implied volatility surface skew and term structure regime read-through; "
        f"front {front.get('dte', '?')}-day ATM IV {front.get('atm')}, "
        f"25-delta skew {front.get('skew_25d')}, 5-delta put wing {front.get('put_wing_5d')}; "
        f"term-structure slope {metrics.get('term_slope')}; "
        "dealer gamma vanna charm hedging, downside protection demand, carry vs stress."
    )


def load_kb_context(
    playbook_dir: str | Path = "docs/playbooks",
    *,
    max_chars: int = 6000,
    session: Session | None = None,
    llm: LLMProvider | None = None,
    query: str | None = None,
    kind: str = "methodology",
    k: int = 6,
) -> str:
    """Methodology grounding context for the surface read-through.

    When a ``session`` + ``llm`` + ``query`` are supplied, retrieves the nearest
    methodology chunks from the pgvector store (the RAG substrate). Falls back to
    a small hand-picked set of playbook files when retrieval is unavailable or
    returns nothing, so callers always get *some* grounding.
    """
    if session is not None and llm is not None and query:
        try:
            hits = retrieve_chunks(session, llm, query, k=k, kind=kind)
            if hits:
                return format_kb(hits, max_chars=max_chars)
        except Exception as exc:  # retrieval is best-effort grounding — degrade gracefully
            log.warning("surface.kb_retrieval_failed", error=str(exc))
    return _load_kb_from_files(playbook_dir, max_chars=max_chars)


def interpret_surface_llm(
    metrics: dict, llm: LLMProvider, *, kb_text: str = "", model: str | None = None
) -> str:
    """Richer narrative via the LLM, grounded in the methodology playbooks."""
    prompt = SURFACE_INTERPRETATION_PROMPT.format(
        metrics=json.dumps(metrics, indent=2), kb=kb_text or "(none provided)"
    )
    return llm.complete(prompt, model=model, max_tokens=600).strip()


def build_surface_report(
    metrics: dict,
    *,
    flow: FlowSummary | None = None,
    flowsum: pd.DataFrame | None = None,
    structures: list[Structure] | None = None,
) -> str:
    """Compose the full markdown report: surface read + flow + greek-OI + packages.

    ``interpret_surface`` supplies the surface section; each flow section is
    appended only when its data is provided — ``aggregate_flow`` -> flow tilt,
    ``flowsum_by_expiry`` -> greek-OI by expiry, ``detect_structures`` -> notable
    packages. Every section is a descriptive regime read-through, never a trade
    signal (FlashAlpha rule 4).
    """
    sections = [interpret_surface(metrics)]
    if flow is not None:
        sections.append(format_flow_markdown(flow))
    if flowsum is not None and not flowsum.empty:
        sections.append(format_flowsum_markdown(flowsum))
    if structures is not None:
        sections.append(format_structures_markdown(structures))
    return "\n\n".join(sections)
