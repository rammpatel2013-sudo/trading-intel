# Expected-Move Break → Gamma Burn-Off → Post-OPEX Re-Entry — Concept Map for `trading-intel`

*Compiled 2026-07-18 from a Doc McGraw ("McDrama McGraw") Substack post on NFLX, read the weekend **after** July monthly OPEX (Fri 2026-07-17). Purpose: distill the durable mechanism, map it onto the descriptors/tools we already ship, and flag the genuine build gaps + the exact data to pull to **find** this trade. Scope for this pass = knowledge + gap analysis only (no code, no live pulls), per the session decision.*

> **Timing note that makes this live-relevant.** McGraw wrote the post the day before Fri July OPEX ("this time, that clock runs through Friday… I may wait until after the weekend when the July gamma has finished burning off"). Today (2026-07-18) is exactly that weekend. So the post-OPEX **re-entry study window** he flags is *now* — the front-expiry gamma that amplified the earnings flush has just expired.

---

## TL;DR — the one durable claim

**An earnings move that breaks *beyond* the options-implied expected move tends to over-realize (persist) until the near-term options structure that amplified it has been hedged, decayed, or expired.** The convex, hedge-*forcing* gamma sits in the **front expiry** (here: July monthly). While it's live, dealer hedging is mechanical, convex, and self-reinforcing (the flush). Once spot moves far enough through the strikes, in-the-money options go **delta-like** and the move's *character* shifts from convex → **linear**. Post-earnings **IV crush** + expiry decay then consume the dangerous near-the-money gamma, which can *paradoxically stabilize* the name. Then the book **rebuilds** at new strikes around the lower price — new dealer hedges, new vol surface, new "normal" — and the *next* convex opportunity is typically a **defined-risk UPSIDE structure** aimed at the next meaningful concentration of call strikes.

Two things to hold onto:

1. **~80% of this is already built** as descriptors/tools. The mechanism decomposes almost perfectly onto our VS3D-lineage stack.
2. **The thesis is about structure, not direction** — same discipline as the dispersion caveat in `vol-newsletter-digest-2026-07-11.md`. It does **not** say NFLX must bounce. It says the amplifier is temporary and the *edge is in the transition*, timed to the expiry clock. Conflating "the amplifier expired" with "it will reverse" is the trap.

**FlashAlpha rule 4 stays intact.** Nothing here alerts on a raw GEX flip or DEX migration. The tradable object is the **EM-break event + the structure-transition + a validated re-entry scan** — descriptor + strategy-scanner territory, exactly where alerts are allowed to live.

---

## 1. The mechanism, decomposed into six mechanical stages

McGraw's prose is one continuous argument; these are the discrete, testable links.

1. **Expected-move break.** The earnings gap exceeds the options market's implied range. "The move often does not politely stop at the edge of the calculation. It tends to persist and over-realize." → *A move beyond the straddle-implied range is the trigger, and its magnitude (how far beyond) is the strength.*

2. **Delta shock — the first echo.** The gap drags strikes from OTM toward ATM/ITM. "Dealers had to adjust their stock hedges, and that adjustment can amplify the original move." → *A discrete re-hedge impulse, largest right at the gap.*

3. **Near-term gamma — the second echo.** "The most concentrated near-term gamma… is sitting in the July options expiring Friday." Gamma is **highest near the strike**; the front expiry carries the convexity that forces increasingly aggressive hedging as price moves. → *The amplifier lives in the front-DTE bucket, and it has an expiry date.*

4. **Mechanical → linear transition.** "Options that are now well in-the-money have already accumulated most of their directional delta… the hedge does not need to change as violently with every additional dollar." → *Once spot exits the strike cluster, positions are delta-like; the move can persist but stops being convex/self-reinforcing.*

5. **Vol reset.** "Post-earnings implied volatility collapses. Short-dated options decay rapidly… The most dangerous near-the-money gamma gets consumed as price moves away from the original strikes." → *IV crush + short-dated theta + expiry remove the amplifier; this can stabilize the name.*

6. **Book rebuild (post-OPEX).** "New strikes become relevant. New positions are opened. Dealer hedges are recalibrated around a lower price… a new volatility surface and a new definition of 'normal.'" → *The re-entry window: a defined-risk upside structure toward the next call-strike concentration, positioned for stabilization / mean-reversion / migration.*

**The play, in his words:** let the front OPEX pass, let the dust settle over the weekend, *then* read where the dealer book reset and where the next pockets of gamma are forming. "The next convex opportunity may not be another chase lower. It may be a defined-risk upside structure."

---

## 2. Concept → `trading-intel` mapping

| # | Mechanic | Our descriptor / tool | What it shows | Status |
|---|----------|----------------------|---------------|--------|
| 1 | Expected-move break | `greeks/straddle.py::atm_straddle` → **`get_straddle`** (accepts a DTE-sliced chain, e.g. ~30-DTE) | `spot ± straddle` implied range, `straddle_pct`, ATM IV | **Have the range**; missing the *pre-earnings snapshot* + the *realized-gap comparison* (Gap G2) |
| 2 | Delta shock (first echo) | `greeks/delta_flow.py` → **`get_delta_flow`**; **`get_live_gex`**; `greeks/gamma_regime.py` | Net DEX lean, short-vs-long gamma regime (amplify vs damp) | **Have descriptors**; not framed as an earnings-gap event |
| 3 | Near-term gamma (second echo) | **`get_gex_term`** (per-expiration GEX by DTE via `gex_term`); `greeks/gamma_profile.py` (spot-ladder $gamma per expiry); **`get_walls`** | Where gamma sits along the curve; the front-DTE share; call/put walls | **Have** — this is the core lens |
| 4 | Mechanical → linear | `greeks/gamma_profile.py` (recompute BS gamma across a spot ladder, per expiry — ADR-002) | Gamma concentration collapsing as spot exits the strike cluster; gamma-flip level | **Have the curve**; no explicit phase classifier (Gap G3) |
| 5 | Vol reset / IV crush | **`get_iv_tenor`** (forward const-maturity IV term); **`get_vol_richness`** (VRP, IV/RV); `greeks/straddle.py::straddle_decay` → labels `{decaying, repricing_up, flat}` + `charm_supported` | Front-end IV collapse; is the straddle bleeding (reset underway) vs richening | **Have**; no earnings-anchored crush magnitude (pre vs post) |
| 6 | Book rebuild (post-OPEX) | **`get_oi_changes`** (ΔOI = opening/closing) paired with ΔIV; **`get_walls`** (new call/put walls); **`get_live_gex`** | New strikes opening, demand-led (buy) vs supply-led (write), wall migration, dealer re-lengthening | **Have** — read via the `oi-flow-direction` rule |
| — | Re-entry structure | `swing/scoring.py` (Stage-1 composite: trend/RSI/DEX/IV_RV/GEX) + `strategies/swing_options.py` (Track A defined-risk debit) | High-PoP defined-risk upside candidate toward the call wall | **Have the engine**; no post-earnings-specific feeder (Gap G4) |

The `straddle_decay` label deserves a callout: its `{decaying / repricing_up / flat}` output is *exactly* McGraw's stage-5 read. `decaying` = the vol reset is underway (amplifier bleeding out); `repricing_up` = vol is richening and other flows dominate (reset **not** done — don't front-run the re-entry). That's a free, already-shipped gate on the re-entry timing.

---

## 3. What this confirms about our architecture

- **FlashAlpha rule 4 is the right call, again.** No single Greek exposure is the signal; the flush is a *composite* event (EM-break + short-gamma regime + front-DTE concentration) and the re-entry is a *validated scan*. Descriptors describe the regime; the strategy layer decides. This post is another independent witness for the rule.
- **The VS3D lens is the correct one.** Per-expiry `get_gex_term` + the spot-ladder `gamma_profile` curve are precisely how you *see* "front-expiry convexity that burns off at OPEX" and "gamma highest near the strike, ITM goes delta-like." We already built the instruments for stages 3–4; see `vs3d-dealer-exposure-digest.md`.
- **We already carry the vol-reset cross-check.** `straddle_decay` + `get_vol_richness` (VRP) + `get_iv_tenor` cover stage 5 without a new metric.
- **The re-entry has a home.** Track A (`swing/`) is a defined-risk high-PoP debit engine — the natural consumer of a post-earnings stabilization candidate. We don't need a new strategy family, just a feeder that hands it the setup.

---

## 4. Gaps worth building (rule-compliant), prioritized

All four are pure-transform or wiring work on **existing** vendor surfaces — **no new vendor** (rule 1), so no ADR is *required* on vendor grounds. A small ADR for the earnings-anchor table is reasonable since it introduces a new persisted concept.

**G1 — Earnings-date anchor (wire `earn_cal`).** The endpoint already exists: `clients/convex_app.py::earnings_calendar(days)` → `GET /api/data/earn_cal`. It is **not** wired to a job, table, or MCP tool. Without earnings dates we can't (a) know a name is "post-earnings," (b) snapshot the *pre-earnings* straddle, or (c) window the over-realization. This is the keystone gap. → new `earnings_calendar` table + idempotent collector job (rule 5) + a `get_earnings_calendar` MCP read. Note: transcript/inflection infra already exists (`trading_intel/earnings/`), but that's *text/tone*, not *dates* — different axis.

**G2 — Expected-move-break detector (pure transform).** Compare the pre-earnings implied range (30-DTE `atm_straddle`) to the realized post-earnings gap (`get_technicals` / price history) → `break = gap% / expected_move%` (a "sigma-beyond-straddle" number), plus an **over-realization** flag that tracks whether the move keeps extending through the front-expiry window. Pure + unit-testable, like `rv_rolloff_projection`. This is the actual **trigger** for the whole pattern.

**G3 — Front-expiry gamma burn-off tracker.** From `get_gex_term`, compute the **front-DTE gamma share** (front expiry's |GEX| ÷ total) and its day-over-day decay into OPEX; from `gamma_profile`, flag the **mechanical → linear** transition when spot has exited the dominant strike cluster (gamma at spot << peak gamma). Emit an expiry countdown. This tells you *where in stages 3–5* the name is.

**G4 — Post-earnings re-entry composite → Track A feeder.** Combine the stabilization conditions into one gate that hands qualifying names to `swing/scoring.py`: (i) EM-break confirmed [G2], (ii) front-expiry gamma expired / burned off [G3], (iii) vol reset underway — `straddle_decay == decaying` and VRP normalizing [`get_vol_richness`], (iv) spot near/through the **put wall** (support) with the next **call wall** as the target/magnet [`get_walls`], (v) dealer regime flipping back toward long-gamma (damping) [`get_live_gex` sign]. Output: a defined-risk upside candidate with the call wall as the objective and the expected-move range sizing the structure.

Sequencing: **G1 → G2 → G3 → G4** (each depends on the prior). G1+G2 alone already make the pattern *detectable*; G3+G4 make it *actionable*.

---

## 5. What data we need to *find* this trade — the checklist

The ordered pull that answers "is this the McGraw setup, and where in it are we?" Each step names the exact tool (all read-only MCP tools; today they'd return Fri EOD data since markets are closed).

1. **Is it post-earnings, and did it break the EM?**
   `earn_cal` earnings date (→ G1) · **pre-earnings** 30-DTE `get_straddle` for the implied range · realized gap from `get_technicals` → `gap% / expected_move%`. *A break needs the ratio > ~1 (moved beyond the straddle).* Today, without G1, this is done manually: NFLX's last earnings date + the pre-print straddle.
2. **Where's the front gamma, and did it burn off Friday?**
   `get_gex_term` → front-DTE (July) share of total |GEX| · `gamma_profile` → concentration vs spot · `get_walls` → call/put walls. *Post-OPEX the July bucket should have collapsed.*
3. **Is the mechanical phase done (convex → linear)?**
   `gamma_profile` → is spot now well outside the original strike cluster (gamma-at-spot << peak)? · `get_live_gex` → has the dealer sign flipped short → long gamma (amplify → damp)? · `get_delta_flow` → residual DEX lean.
4. **Is the vol reset underway?**
   `get_iv_tenor` → front-end IV crushed vs back? · `get_vol_richness` → VRP normalizing? · `straddle_decay` label = `decaying` (reset live) vs `repricing_up` (wait).
5. **Is the book rebuilding, and which way?**
   `get_oi_changes` (ΔOI opening/closing) **paired with ΔIV** → demand-led (buy) vs supply-led (write), per the `oi-flow-direction` rule — *do not read ΔOI as direction alone* · `get_walls` → have new call/put walls formed around the lower price?
6. **What's the target and the structure?**
   Next **call wall** = the upside magnet · expected-move range (`get_straddle`) sizes a **defined-risk debit** structure · route through `swing/scoring.py` (Track A) for the conviction score + candidate structure.

**Timing rule (bake it in):** study the setup the **weekend after front-month OPEX** — McGraw's "let the dust settle." The re-entry read is cleanest once the amplifying expiry is gone, which for the July example is *this* weekend.

**One data caveat, same as VS3D:** our dealer sign is *inferred* (`_SIGN` in `gamma_profile.py` / exposures), not cleared-participant data. The stage-3/4 "dealer flips long gamma" read is a model inference, not observed positioning — weight it accordingly (see `vs3d-dealer-exposure-digest.md` §5).

---

## 6. Cross-references

- `docs/learning/vs3d-dealer-exposure-digest.md` — dealer gamma/charm framework; the `gamma_profile` spot-ladder + `straddle_decay` cross-check; inferred-vs-cleared caveat.
- `docs/learning/vol-newsletter-digest-2026-07-11.md` — same author (Doc McGraw); RV window roll-off, VIX Shift-vs-Slide decomposition, the "structure not direction" discipline.
- MEMORY: `vol-newsletter-sources`, `swing-trade-system-build` (Track A engine for the re-entry), `earnings-transcript-inflection` (text/tone infra — *not* earnings dates), `oi-flow-direction` (ΔOI ≠ direction), `index-etf-gex-dex-only`.
- `CLAUDE.md` rule 4 (FlashAlpha — descriptors ≠ signals), rule 5 (idempotent jobs — the `earn_cal` collector), rule 7 (local-LLM only in scheduled paths).

---

*Concept digest only — no code written and no live data pulled this pass (per session scope). Build sequence when ready: G1 (earn_cal anchor) → G2 (EM-break trigger) → G3 (burn-off tracker) → G4 (Track A re-entry feeder).*
