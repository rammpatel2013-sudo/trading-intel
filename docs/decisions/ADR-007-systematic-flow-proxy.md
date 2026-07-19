# ADR-007 — Systematic vol-sensitive flow proxy (assumption-based descriptor)

**Status:** Accepted (2026-07-18)
**Related:** ADR-006, `docs/em-break-system-plan.md`, `docs/learning/em-break-gamma-burnoff-digest.md`, MEMORY `no-ibkr-api`, `trend-collection-buildout`

## Context

The re-entry pattern's market-wide tailwind is the mechanical buying of vol-sensitive systematic funds (vol-control / target-vol, plus the inverse-vol sizing inside CTAs and risk-parity) as realized vol rolls off. We already had the RV roll-off engine (`prices/realized_vol.rv_rolloff_projection`) but no translation from an RV path to a buying-pressure figure.

Estimating that flow requires parameters that are inherently uncertain: cohort AUM, target vol, and the realized-vol estimator convention. These are third-party desk estimates, not measured positioning — the same epistemic status as our *inferred* dealer sign (`_SIGN`, cf. the VS3D caveat).

## Decision

1. **Build the flow proxy as a descriptor** (`trading_intel/flows/`), never a standalone signal (rule 4). Exposure is inverse-vol with a cap, `w(rv) = clip(target/rv, 0, w_max)`; the Δexposure over the projected RV path × assumed AUM gives the $ figure, and `dw/drv = -target/rv²` gives the convexity (where buying accelerates).
2. **Keep the assumptions in one visible place** — `flows/registry.py` cohorts, every row `verify=True`, overridable from `Settings` (`VOL_CONTROL_AUM`, `VOL_TARGET`, `CTA_AUM`, `RISK_PARITY_AUM`). No hard-coded magic numbers scattered downstream.
3. **Treat the $ output as order-of-magnitude.** Consume it as a **sign + cross-sectional/banked percentile**, not a hard dollar amount (the MCP tools carry this caveat). This mirrors the `no-ibkr-api` percentile-bank-forward stance.
4. **Model it index-level.** Vol-control buys the index, not single names; a name participates only via its index weight (and dispersion means one name's earnings move barely moves index RV). The single-name re-entry trigger stays the dealer gamma flip + overwriter re-supply; systematic flow is context.
5. **No new vendor.** RV comes from the existing `quotes_daily`; AUM is a static registry constant (like the FRAWD leverage registry).

## Consequences

- We get a usable, transparent tailwind read now, with the uncertainty made explicit rather than hidden in a fitted number.
- The absolute $ is not trustworthy until the AUM/target assumptions are calibrated against current desk estimates; the plan flags this and the tools degrade to sign/percentile.
- Adding cohorts or re-calibrating is a one-file change (`registry.py`) — no schema, no vendor.

## Alternatives considered

- *Skip the flow model, keep only `rv_rolloff`* — rejected; the user explicitly wanted the full systematic-flow stack, and the convexity/AUM translation is the actionable part.
- *Source real positioning data* — unavailable without a new vendor (rejected under rule 1); inference with an explicit caveat is the honest interim.
