# VS3D (VolSignals 3D) — Dealer-Exposure Framework: Concept Map for `trading-intel`

*Compiled 2026-07-15 from the VS3D onboarding guide (`vs3d-onboarding-webinars.netlify.app`, 7 chapters + glossary). Source is a market-maker's framework for reading SPX/VIX dealer hedging flows, built by Dan (ex-Belvedere SPX lead MM) + Matt (ex-sell-side index vol). Purpose: distill the durable, mechanism-level ideas and map them onto our GEX/DEX/VEX/CHEX stack — flagging what we already compute vs. genuine gaps, and keeping every suggestion inside the CLAUDE.md rulebook (FlashAlpha rule 4 especially).*

> **Scope note.** VS3D is **intraday, 0DTE-dominant, SPX/VIX-only**. Most of `trading-intel` is a **1–6 month swing** system over a multi-name watchlist. The overlap is narrow but real: our **index handling** (SPY/QQQ/SPX net GEX/DEX), the **`2_Intraday_0DTE` page**, the **forward gamma/charm field**, and the **MM gamma profile**. Read the mapping through that lens — a lot of VS3D's per-strike SPX machinery lands on code paths we currently *exclude* for index roots (see the caveat in §5).

---

## TL;DR

1. **The vocabulary is a near-exact match, and we already compute the core.** VS3D's four visualized greeks (delta, gamma, charm, vanna) are our DEX / GEX / CHEX / VEX. `greeks/exposures.py` computes all four net-signed; `flip_point.py` + `walls.py` give the flip level and call/put walls; `gamma_profile.py` is *literally* a "VS3D-style $Gamma-vs-spot curve" (its own docstring); `forward_field.py` re-simulates gamma/charm forward in time. We are well inside this framework already.
2. **VS3D independently validates FlashAlpha rule 4.** Dan's central warning — "gamma is *behavior, not direction*," a flip crossing alone is not a trade, "negative gamma is a **force multiplier, not a force generator**," single-factor confluence is a skip — is the same conclusion our rulebook reached: **GEX/DEX/VEX/CHEX are regime descriptors, not signals.** A desk trader and a backtest arrived at the same place. Good to have the confirmation on record.
3. **Genuine, cheap, rule-compliant gaps worth building (priority order):** (a) **ATM straddle price + a "straddle decaying?" flag** — VS3D's single most-used tool and our #1 missing primitive (pure chain transform, we don't compute it anywhere); (b) **Speed (dGamma/dSpot)** and **Color (dGamma/dTime)** — third-order descriptors that fall out as derivatives of curves we *already* build (`gamma_profile.py`, `forward_field.py`); (c) **charm-flip level** (sign-change of per-strike charm); (d) **volga/vomma** (already ROADMAP item 9; the `vommaxoi` field is already pulled, just unrendered). All are descriptors → they must **not** emit alerts directly (rule 4).
4. **The one caveat that matters most:** VS3D's entire edge is **participant-level exchange clearing data** (customer/firm/MM cleared buys & sells → *net hedgeable quantity*). Our dealer positioning is the **`_SIGN={C:+1,P:−1}` convention** — the exact "long-call/short-put = dealer" assumption VS3D calls "massively inflated." We *cannot* close that gap without a new vendor (rule 1 → ADR). The honest read: this is **why** FlashAlpha rule 4 exists — treat our exposures as a directional proxy, not a measured dealer book. See §5.

---

## 1. Core concepts distilled (the durable mechanisms)

### Gamma — behavior, not direction
- **Long-gamma dealer** (net long options) sells rallies / buys dips → **dampens** vol → support/resistance, pinning. **Short-gamma dealer** buys rallies / sells dips → **amplifies** → acceleration, "air pockets." This is absorption, *not* active selling: "all they're doing is fielding the bid."
- **Negative gamma needs a trigger.** Without an initiating imbalance a short-gamma tape "just floats around and looks like positive gamma until Trump tweets and it spikes 200 bucks." → *never alert on a gamma-regime state alone.*
- **Asymptotic gamma near expiry** is why 0DTE dominates the profile even though it's a sliver of total OI. Near the close, model gamma goes "stupid" ($55B readings) — a micro-resolution artifact, not signal.

### Charm — the tradable directional flow
- **Charm (dDelta/dTime)** is a *passive, biased, TWAP-like* flow as options decay their delta toward 0 (OTM) or 100 (ITM). Unlike gamma it **is directional**. Dan's "water carving a canyon" — small repetitive flow beats big splashy flow.
- **Afternoon sweet spot 1:30–3:00pm ET:** U-shaped volume means external flow is thin midday *and* decay accelerates → charm's cleanest window. Avoid at the open (9:30–11:00).
- **Mandatory cross-check: is the ATM straddle *decaying*?** If the straddle is repricing *up*, charm is being overpowered — "the snake-oil tell." Charm is a *weighted coin*, valid only absent a strong competing flow.
- **Gamma can absorb charm:** a single big dealer-long strike with 65% of its hedge still to go can swallow 400 futures of charm flow → predicted pins land *off* the biggest cluster.

### Vanna & Volga — the vol-driven channel
- **Vanna (dDelta/dVol)** = charm's two-way cousin (vol can rise *or* fall). A vol *drop* acts like time passing (buy flow); a vol *spike* reverses it. Matters at the open and in **high VIX (20+)**, where it can dominate charm; near-irrelevant at low VIX. **0DTE vanna is "ephemeral" — Dan says ignore it.**
- **Volga = gamma of VIX options.** When there's both volga and SPX vanna, **VIX gamma converts into SPX gamma** via the vanna channel — a *secondary* hedging path not visible in the underlying's own options. Proxy to track it cheaply: **1-month skew on a percentile basis** (we already have this — see §2).

### Positions — tests, anchors, structure
- **Put = Call once hedged.** Only two things matter per strike: proximity to spot, and **is the dealer long or short it.**
- **Long option = ANCHOR** (positive gamma resistance + charm magnetism → pins price, intensifies into close). **Short option = TEST** (negative gamma repels; you can *never* pin on a short strike). Test-Anchor frame: upper test = short cluster above, lower test = short cluster below, anchor = long cluster between; **spot ± straddle** ≈ the range.
- **Structure > single strike**, and the **opening position is sticky** (structural firms hold; HFT/customer flow washes out and reverts). Study the 9am profile. **Fishbone** (alternating long/short) = degraded signal → size down or sit out.
- **Charm-flip level** = strike where per-strike charm changes sign; a binary decision point (through it → decay supports continuation; stuck before it → decay fights you).

### Model vs simulated greeks
- **Model** = instantaneous Black-Scholes. **Simulated** = finite-difference (bump spot $5 or advance the clock 5 min, re-measure delta). Simulated normalizes distortions from complex/"fishbone" books (e.g. an iron condor whose +/− gamma cancels over a realistic range).

### Desk realities (Ch. 6)
- Most large SPX orders arrive **already hedged** ("tied/laid up") — the futures impact happened *hours before* the print. **Tape/time-and-sales inference reads SPX backwards.** Hedging is a sub-second automated machine → dealer positioning is **prescriptive** (a "code" for the next day/week), not a guess.
- Honest ceiling: **~65% directional reliability.** Edge is in *structure selection* (spreads/flies in +gamma; single-leg convexity in −gamma), not win rate. "One big chunk of the puzzle" — layer with VWAP/TPO/pivots.

---

## 2. Concept → `trading-intel` mapping

**Already built (strong):**

| VS3D concept | What it is | Our status | Where |
|---|---|---|---|
| Gamma exposure (GEX) | Net dealer gamma | ✅ `Σ sign·gxoi` | `greeks/exposures.py::compute_exposures` → `gex_total`; MCP `get_gamma_history`, `get_live_gex` |
| Delta exposure (DEX) | Net dealer delta | ✅ `Σ dxoi` | same → `dex_total`; MCP `get_delta_flow` |
| Vanna exposure (VEX) | Delta sens. to vol | ✅ `Σ vanna·oi·spot·iv` | same → `vex_total` (recomputed from raw vanna, not Convex `vxoi`) |
| Charm exposure (CHEX) | Delta decay flow | ✅ `Σ charm·oi·spot·365` | same → `chex_total` |
| Vanna (greek) | dDelta/dVol | ✅ analytic + native | `black_scholes.py::bs_vanna` (validated 3 ways); Convex `vanna`; CVForge synthesizes it |
| Charm (greek) | dDelta/dTime | ✅ analytic + native | `black_scholes.py::bs_charm`; Convex `charm` |
| Gamma flip / zero-gamma | Sign-change spot | ✅ brentq + spot-ladder | `greeks/flip_point.py::gex_flip`; `gamma_profile.py` all-curve zero-crossing |
| Call wall / put wall | Max gamma-OI strike per side | ✅ unsigned `gxoi` by strike | `greeks/walls.py::compute_walls`; MCP `get_walls` |
| $Gamma-vs-spot curve (spot re-sim) | "Gradient / Gamma Chart" | ✅ **cites VS3D by name** | `greeks/gamma_profile.py` (81-pt spot ladder); page `15_MM_Gamma_Profile` |
| Forward gamma/charm (time re-sim) | "Color"-adjacent evolution | ✅ time grid to 16:00 | `greeks/forward_field.py::forward_field(greek=…)`; page `16_Price_Cone` |
| 1-mo skew percentile (vanna proxy) | Dan's volga/vanna tracker | ✅ | `skew_snapshots`; MCP `get_skew_history`, `get_index_skew`; backfill `scripts/skew_backfill.py` |
| Expected-move range | Spot ± move | ✅ but **via IV·√T, not straddle** | `prices/price_cone.py::forward_cone`; `dashboard/forward_cone_data.py` |
| Model vs simulated greeks | Instantaneous vs re-measured | ⚠️ analytic-BS-reshocked (sticky-strike), not true finite-diff | ADR-002; `flip_point.py`, `gamma_profile.py`, `forward_field.py` |

**Genuine gaps (nothing in the repo):**

| VS3D concept | Definition | Note |
|---|---|---|
| **ATM straddle price** | ATM call + ATM put premium | ❌ none. `straddle` only appears as a *structure classifier* in `strategies/options_flow.py`. Highest-value gap. |
| **"Straddle decaying?" flag** | charm-validity cross-check | ❌ blocked on the above |
| **Speed** (dGamma/dSpot) | how fast gamma builds → "sharp" vs "soft" walls | ❌ none. Falls out as the spatial derivative of `gamma_profile.py`. |
| **Color** (dGamma/dTime) | how gamma intensifies toward close | ❌ none. Falls out as the time derivative of `forward_field.py` gamma grid. |
| **Volga / vomma** | gamma of vol (VIX→SPX channel) | ⚠️ `vommaxoi` pulled (`convex.py` `_FLOWSUM_PARAMS`) but **not aggregated or rendered**. Already ROADMAP item 9. |
| **Charm-flip level** | strike where charm changes sign | ❌ none. Derivable from `forward_field(greek="charm")` per-strike. |
| **Pinning-strength / magnet metric** | long-cluster pin score | ❌ walls are the *implicit* pins; no explicit strength metric |
| **Test/anchor split** | short-cluster (test) vs long-cluster (anchor) levels | ❌ our walls don't separate dealer-long from dealer-short clusters (ties to §5) |
| **Participant-type positioning** | customer/firm/MM cleared imbalance | ❌ not available from our vendors — see §5 |

---

## 3. What VS3D confirms about our architecture

Worth internalizing because it's external, desk-level validation of choices already in `CLAUDE.md`:

- **Rule 4 is correct.** "GEX/DEX/VEX/CHEX are regime descriptors, not signals … do not alert on raw GEX flip crossings, DEX migrations, or VEX changes alone." VS3D: gamma is *behavior not direction*; a scary short-gamma regime "churns in place" without a trigger; "single-factor confluence is not a trade." Keep descriptors out of the `signals` table (only `strategies/skew.py` and `strategies/vol_regime.py` write it today).
- **The forward/time dimension is where the edge is.** VS3D's most tradable read is **charm** (directional, afternoon-weighted), and its signature view is a *simulation across spot × time*, not a static snapshot. We already lean this way: `forward_field.py`, the page-14 charm composite weighting charm by `session_fraction_remaining`, and the "charm → 0 at 4pm" note in MEMORY. Invest here over more static per-strike bars.
- **Skew-percentile as a vol-channel proxy** (rather than modeling the whole surface) is exactly Dan's shortcut for vanna/volga — and we already collect it. Reuse it before reaching for a full surface build.

---

## 4. Gaps worth building — rule-compliant next steps

Ordered by value ÷ effort. **All are descriptors → rule 4 forbids them from emitting alerts** unless routed through a validated scanner or the Phase-5+ probability model.

1. **ATM straddle price + straddle-decay flag (do this first).** Pure transform on a chain we already pull: `straddle_atm = atm_call_mid + atm_put_mid`; expose intraday delta to flag decaying vs repricing. Unlocks VS3D's #1 charm cross-check and a straddle-based range (`spot ± straddle`) to sit alongside our IV·√T cone. Note the nuance: **ATM straddle ≈ 0.8σ**, so it's a *tighter* range than our current ±1σ cone — they're complementary, not duplicates. Home: `greeks/` (pure fn + test) → surface via a new read-only MCP tool; render on `2_Intraday_0DTE`.
2. **Speed & Color descriptors (nearly free).** `speed = d(gamma_profile)/d(spot)` off the existing 81-pt ladder; `color = d(forward_field gamma)/d(time)` off the existing time grid. Speed flags **sharp vs soft walls** ("high-speed call wall"); color quantifies the intraday sharpening we currently only describe in prose. Add as columns/overlays on pages 15/16.
3. **Charm-flip level.** Extract the sign-change strike from `forward_field(greek="charm")`. A single scalar per name/expiry; cheap to add next to the gamma flip.
4. **Volga/vomma (already roadmapped).** No ADR needed — it's an existing Convex field. Add `vomma`/`vommaxoi` to `_CHAIN_PARAMS`, aggregate a `VLEX`-style exposure in `exposures.py`, render it. Most useful on the **index/VIX** side (the VIX-gamma→SPX channel), least useful for 0DTE names.
5. **Test/anchor + pin-strength (larger, data-gated).** Requires splitting each strike's dealer-long vs dealer-short interest to label clusters as tests vs anchors. With sign-convention-only data this degrades to "walls = pins"; doing it *properly* runs into §5.

Explicitly **not worth chasing:** heavy 0DTE-vanna modeling (Dan: "ephemeral, ignore"); a true finite-difference greek engine (our sticky-strike BS re-shock already captures most of the "simulated greek" value per ADR-002).

---

## 5. The caveat that reframes everything: inferred vs cleared positioning

VS3D's whole thesis is that the **naive method is wrong**: taking OI and stamping "long-call/short-put = market maker" on it produces "multiples upon multiples" of the real gamma (their BofA reference). Their fix is **OCC/CBOE participant-level clearing data** — customer/non-customer/firm/MM cleared buys and sells — netted to the **net hedgeable quantity** (often ~1% of tape volume, after removing MM-on-MM churn).

**Our exposures are built on precisely the assumption they critique:** `_SIGN = {"C": +1, "P": −1}` in `exposures.py`, `flip_point.py`, `walls.py`, `gamma_profile.py`, `forward_field.py`. We infer the dealer; we don't measure it.

Honest implications:
- **We can't get participant-type clearing data from our current vendor set.** Adding a source for it is a new vendor → **rule 1 → requires an ADR** (`docs/decisions/`). The MASTER_PLAN vendor set is fixed. Don't reach around it.
- **This is the deeper reason rule 4 exists.** Because the dealer book is *inferred*, a raw flip crossing is doubly untrustworthy. Treating GEX/DEX/etc. as regime *descriptors* — and requiring a validated scanner / probability model + VIX + ATM IV + credit spreads before anything fires — is the correct hedge against inference error. VS3D reaches the same "need confluence + a trigger" conclusion from the opposite (better-data) direction.
- **We do have a flow lens VS3D would respect**, and it's arguably closer in spirit to "what are hedgers actually doing" than static OI sign convention: `flowratio`/`vflowratio`/`flownet`, `get_delta_flow`, and the live TAS tape. Note Dan's warning, though — **SPX tape inference reads backwards** because large orders arrive pre-hedged; our per-name equity flow is less prone to this than index tape.
- **Untapped native candidate:** `clients/convex_app.py::matrix()` is documented as a **"Dealer positioning grid"** (POST `/api/data/matrix`) but is currently an **unparsed JSON passthrough**. If any single thing could move us toward a measured (vs. inferred) dealer view without a new vendor, it's parsing that — worth a spike before building §4.5.

**Index-root caveat (do not miss this).** VS3D's per-strike walls / pinning / test-anchor machinery is entirely **SPX**. But per MEMORY `index-etf-gex-dex-only`, we **exclude SPY/QQQ/SPX from per-strike, walls, and skew** via `CHAIN_EXCLUDE_ROOTS` (net GEX/DEX only). So applying VS3D's core framework to SPX in *our* system means first revisiting that exclusion — otherwise the walls/flip/straddle work lands only on single-name equities, which is *not* where VS3D's edge lives.

---

## 6. Cross-references
- MEMORY: `index-etf-gex-dex-only` (the CHAIN_EXCLUDE_ROOTS caveat above), `swing-trade-system-build` (horizon mismatch — VS3D is intraday, we're 1–6mo), `cvforge-api-access` (synthesizes vanna/charm; VEX/CHEX scale calibration pending), `vol-newsletter-sources` (skew/dispersion already built), `oi-flow-direction` (ΔOI ≠ direction — consistent with VS3D's "gamma ≠ direction").
- Roadmap: `docs/ROADMAP.md` item 7 (unified vol-cone/expected-move envelope — fold the straddle range in here), item 9 (volga/vomma).
- Rules: `CLAUDE.md` rule 4 (FlashAlpha), rule 1 (vendor isolation → ADR for participant data), ADR-002 (recompute sanctioned for simulation views only).

*Bottom line: we already speak VS3D's language and compute its core greeks; the framework validates our "descriptors-not-signals" architecture; the cheap wins are the ATM straddle price + speed/color; and the honest limit is that we infer the dealer where VS3D measures it — which is exactly why our alerting discipline is right.*
