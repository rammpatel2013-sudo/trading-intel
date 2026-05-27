# ADR-002: Allow Black-Scholes greek recompute for the spot-ladder MM positioning simulation

**Date:** 2026-05-26
**Status:** Accepted
**Deciders:** Mithil

## Context

The dashboard's gamma views (GEX surface, Live Gamma Map) are built on Convex's
pre-computed `gxoi` (gamma × OI) read at the **current** spot. That is a static,
single-spot snapshot: it answers "where is gamma concentrated by strike right
now," not "what would dealer gamma be **if spot moved** to level X."

VolSignals' VS3D (and similar MM-positioning tools) render a different view: a
**$Gamma-vs-spot-reference profile, decomposed by expiry**, under a sticky-strike
assumption — each option's gamma is re-evaluated across a spot ladder and summed
to the per-expiry and All-Expiries curves. The zero-crossing of the aggregate
curve is the gamma-flip level.

Earlier notes framed "no Black-Scholes recompute" as a hard rule. In fact
MASTER_PLAN already sanctions a small BS module for the simulation grid
(MASTER_PLAN.md "Keep a small `greeks/black_scholes.py` only for the heatmap
simulation grid"), and `greeks/flip_point.py` already does sign-weighted
dollar-gamma **repricing** to locate the flip point:

    net_gex(S) = Σ  sign · Γ_BS(S; K, σ, T) · oi · multiplier · S² · 0.01

So recompute is already in the codebase for one purpose. The open question was
only whether to extend it to a full spot-ladder visualization.

## Decision

**Black-Scholes greek recompute is an accepted technique for simulation/
what-if views.** We build the spot-ladder MM gamma profile (per expiry + All
Expiries, sticky-strike) by sweeping the existing dollar-gamma formula across a
grid of hypothetical spot levels.

Guardrails that remain in force:

- **Convex pre-computed greeks stay the default** for snapshot/by-strike views
  (GEX surface, Live Gamma Map heatmaps). We do **not** recompute those; only the
  explicitly simulated spot-ladder/what-if views recompute.
- The shared BS math lives in one place, `greeks/black_scholes.py`
  (`bs_gamma`, `dollar_gamma`, `years_to_expiry`), reused by `flip_point.py` and
  the new `greeks/gamma_profile.py`. No scattered ad-hoc BS.
- **Sticky-strike** is the stated assumption: each strike keeps its stored IV as
  spot moves (no smile re-solve). This matches the VS3D convention and the
  VIX-decomposition "sticky strike" framing already used in the project.
- Still a **regime descriptor, not a signal** (rule 4). The profile and its flip
  level are descriptive; no alerts are emitted from it.

## Consequences

- Enables the spot-ladder $Gamma profile page and per-expiry decomposition.
- Requires per-expiry data: `live_gex` gains an `expiry`/`dte` column so the
  curve can be split by expiration and a true 0DTE scope isolated (see the
  per-expiry collector change shipped alongside this ADR).
- Simulation cost: a spot grid × chain re-evaluation per render. Kept cheap by
  vectorizing over strikes (NumPy) and a bounded grid; acceptable for the
  near-the-money delta-band chain. (Mirrors MASTER_PLAN risk R9 — cache/recompute
  on refresh only.)
- The phrase "no recompute" in older notes is superseded by this ADR.
