# Vol/Options Newsletter Digest — Knowledge Update for the Dashboard

*Compiled 2026-07-11 from Gmail. Sources: `jaredhstocks@substack.com` (1 email) and `docmcgraw@substack.com` (8 emails). Purpose: distill durable volatility / options / 0DTE / RV-HV concepts and map them onto `trading-intel` dashboard metrics, flagging what we already track vs. genuine gaps.*

> **⚠️ CORRECTION (2026-07-11, after a code-level review of the repo).** My first pass claimed the dispersion/implied-correlation metric was a missing "blind spot." **That was wrong — the dispersion stack already exists end-to-end** and was clearly built from earlier Doc McGraw reading: COR1M/COR3M (migration `0025`), VIXEQ/DSPX/`vixeq_vix_spread` (migration `0027`) are columns on `index_skew_daily`, fetched by `clients/prices.py::fetch_cor1m/cor3m/vixeq/dspx` (Yahoo ^COR1M/^COR3M/^VIXEQ/^DSPX), populated by `scheduler/jobs/index_skew.py`, narrated by `vol/eod_narrative.py::dispersion_phrase` ("in Doc's framing"), and rendered in the EOD vol report's **COR1M Map** tab + the **8_VIX** dashboard page. There's even a `setup_cor_vixex.bat`. The **only** real code gap was that the `get_index_skew` **MCP tool didn't surface those stored fields** — now fixed (see §3). The TL;DR/§3 below are corrected accordingly.

---

## TL;DR — what was actually actioned

1. **Dispersion / implied correlation — already built; the gap was MCP exposure (FIXED).** Both authors are fixated on it right now: COR1M near record lows (~4–5 handle), VIXEQ (single-stock vol) ~50 vs VIX ~17 — a ~33-pt wedge "among the widest ever." The data was already collected/stored/charted; it just wasn't reachable from Claude Desktop. `get_index_skew` now emits `cor1m/cor3m/cor_slope/vixeq/dspx/vixeq_vix_spread` + percentiles.
2. **"Realized-vol window roll-off" projection — genuinely new; BUILT.** Doc's mechanical point: the big early-June down days age out of the trailing 21-day RV window around **July 13**, dragging measured RV toward ~13 and feeding systematic buying — then the floor becomes a launchpad. Added `prices/realized_vol.py::rv_rolloff_projection` (pure transform + tests) that projects the trailing-window RV drift as past returns age out. Wiring to an MCP tool / dashboard panel is the remaining step.
3. **Several posts NOT yet in graphify — prepped for ingest.** The KB already has ~10 older Doc McGraw posts, but the recent/evergreen ones below are missing; 4 are converted to `.docx` and ready to drop into `research/doc/` + `sync_knowledge` (see §5).

---

## 1. Core concepts distilled

### Volatility clustering & regime persistence (jaredhstocks, Jun 30)
- **Low vol induces lower vol; high vol persists too.** Vol is not randomly distributed — calm and turbulent states cluster and persist. Statistically it's a better trade to *short* low vol than to *buy* it ("I got steamrolled buying vol <15").
- **Asymmetric mean reversion:** clustering lasts *longer to the downside* (vol grinding lower) because of normal spot/vol correlation + equity upside skew. But when VIX is elevated, the next move is *more likely up than down* — "a VIX at 35 is more likely to print 40 than 25." This is why vol desks buy VIX at 35 even after a run from 20.
- **RV-from-VIX shortcut:** `daily move% ≈ realized_vol / 16`, where 16 ≈ √252. Example: RV of 4 → SPX averaging ~0.25%/day (the 2017 regime).

### Variance Risk Premium (VRP = IV − RV) (jaredhstocks, Jun 30)
- Jared flagged VRP at **−5 pts** (IV *below* realized) as "quite large," the kind of print seen after steep/quick SPX drawdowns. **Base-case bias: vols decay further** (lower close-close RV pulls the basis back to mean) rather than IV rising into RV — especially with /VX in contango.
- ⚠️ **Sign-convention note for us:** Jared uses `IV − RV` (negative = IV cheap vs RV). Our `get_vix.vrp` currently reads **+5.6** and `get_vol_richness.vrp_pts` uses `iv_atm − fcst_rv`. Confirm we're internally consistent and label the sign explicitly on the dashboard so a "-5" from a newsletter maps cleanly onto our number.

### Dispersion, implied correlation, VIXEQ, DSPX (both authors — the current obsession)
- **The mechanic:** an index is an average; when constituents move *independently* (low correlation), their moves cancel and the index goes quiet even while single names swing 5–10%. "Sleepy surface, thrashing underneath."
- **The metrics:**
  - **COR1M** = 1-month implied correlation index. Near **4–5** now = about as low as it goes.
  - **VIXEQ** = single-stock vol index (~50). **VIXEQ − VIX wedge ~33 pts** = "among the widest ever." VIXEQ running ~2.3× index VIX.
  - **DSPX** = CBOE dispersion index, at fresh highs (crisis-level readings, but at the *highs*, not in a hole).
- **The trade:** vol desks/systematic funds **sell index vol, buy single-stock vol** — betting the parts move but the whole stays contained. Works when mega-caps disagree; hurts when they all tell the same story.
- **Critical framing (don't botch this):** *dispersion is a statement about **structure, not direction.*** It says the spring is loaded — a shock, if one arrives, travels further than usual. It does **not** say a shock is coming. Conflating the two is how people talk themselves into permanent bearishness.
- **The unwind signature:** if it unwinds, desks buy index vol + sell constituent vol → **VIX rising and SPX selling *arrive together*.** Doc's explicit tell: "SPX selling and VIX rising together. Not one then the other. Both at once."
- **Empirical base rate (Doc's sample, DSPX >35 + crushed correlation):** 6 instances → 3 benign (bled/chopped/recovered), 3 ugly (−10 to −20%+ over 2 weeks). The split had **nothing to do with the dispersion reading itself** — the ugly ones all had an *outside catalyst* (usually a rates break; once an oil shock that hooked into rates). Damage built in the *back half*, after the trigger, as mechanical unwinds fed on themselves.

### Shift vs Slide — VIX decomposition (Doc, Feb 16 + Feb 25 + KB doc 17)
Decompose a daily VIX change into *why* it moved:
- **Shift = Sticky Strike (mechanical).** Each strike's IV is "on the menu" and fixed; when spot moves, you just end up standing in front of a different strike's IV. Nobody changed their vol opinion. Example: SPX +3.4 pts mechanically pushed VIX **+0.09** with no regime change.
- **Slide = Sticky Delta / Parallel Shift (regime).** The whole IV surface reprices together — "the cost of fear" rises/falls across all strikes. Example: Friday's rally pulled the entire curve down **−0.24** ("sliding down the skew").
- **Why it matters:** if VIX is moving on *sticky strike*, the surface isn't really changing (mechanical, not emotional). If it's moving on *parallel shift*, the market is repricing fear = **regime signal**. When slide dominates, rallies compress vol and selloffs expand it *reflexively*.
- Doc references a live **"VIX Decomposition table"** (Sticky Strike / Parallel Shift / Convexity up-down) as a standing tool — a model for a dashboard panel (see §3).

### VIX term structure & the VIX complex (both + KB doc 23)
- **Contango** (front < back months) = the calm regime; **backwardation** = stress. Jared notes /VX in contango explains why SPX fell 400 pts off the June 2 top while VIX rose only 0.5.
- **VIX9D vs VIX** separates "nervous about *this week*" (event risk) from "the *regime* is breaking." VIX9D > VIX = near-term event premium.
- **VVIX** = vol-of-vol; elevated VVIX = uncertainty about the *path* of vol. Doc: "VVIX falling on a down day" = distribution, not panic.
- **VIX1D** = current-session expected vol from 0DTE/1DTE; low VIX1D vs VIX confirms intraday quiet; a VIX1D intraday spike-then-collapse = hedging monetized fast, vol supply still thick.
- **Election-vol seasonality (Jared):** midterm vol hanging ~20 with slight Nov–Dec backwardation (0.1 pt). Historically within a 30-TD sample, **vol peaks ~3 trading days before the election, then normalizes.**

### 0DTE & expiration mechanics (Doc, Jul 11 + KB docs 15, 20)
- **Monthly OPEX strips dealer hedging** that acts as the market's shock absorber — "the pin comes off." The window *after* OPEX is when support thins.
- **VIX expiration** resets the vol complex.
- **0DTE reshapes the gamma landscape intraday** — a midday reset can shift the effective zero-gamma level by several points as 0DTE positions roll.
- **Doc's Jul 13–22 stacked-catalyst example:** RV window roll-off (~13th) → CPI (14th, rates trigger) → big-bank earnings (14–15th, feeds dispersion) → July OPEX (17th, shock absorbers removed) → VIX expiration (22nd). "Mechanical dates force unwinds; macro dates supply the spark."

### Anatomy of a VIX trade — structure (Doc, Jan 10, free educational)
- Thesis was "VIX too low" (spot 13.38 vs /VX future 16.7). Structure used: **Broken-Wing Butterfly** 13.5 / 17 / 22.5 (3.5 × 5.5 width) to be right on *direction* while managing *when/how much*.
- Body (short 17s) carries the P&L; outer wing (22.5) ~worthless as expected. Later added a naked 20 call "hat" for extra credit, lowering cost basis.
- Key lesson for our purposes: **spot VIX vs /VX front-month basis is the setup screen**; structure choice manages the timing uncertainty. ⚠️ Naked VIX options are dangerous — educational, not a signal.

### The "6 Pillars of the Tape" framework (Doc, May 25) — a structural checklist
A classic-TA framework (Greg Capra) with a dealer-positioning layer under each pillar:
1. **Market comes first** → + **Gamma regime** (above zero-gamma flip = dealers long gamma = mechanical "gamma shield" bid).
2. **Trend alignment** → + **Systematic tailwind** (falling VIX ⇒ vol-target funds/CTAs *mandated* to add equity exposure).
3. **Market internals** → + **Tick as a flow meter** (Tick +1000 into a call wall = the flow pushing the level in real time).
4. **Sector rotation** → + **Dispersion read** (rotation *with vol dropping* can be a structural dispersion rebalance, not a defensive sell signal).
5. **Intermarket analysis** → + **Volatility relationships** (bond/gold/oil/equity vol as one web; is the *whole* vol web repricing or is fear contained to one corner?).
6. **Stop predicting, start aligning** → + **Gamma profile** (read where dealer flows fight vs. feed price *before* the move).

### Macro backdrop (Doc, May 10 "Tao of the Tape") — context, not dashboard signal
Structural-resilience thesis for 2026: geopolitical risk premium is a *rental not a purchase*; US energy independence as a grounding wire; energy intensity down ~70% since 1980 (no 1970s-style stagflation); consumer "trading down" but still spending; IPO/SPAC supply as a quiet brake on the S&P; **private credit / SaaS-lending liquidity mismatch (PIK interest, stale marks, gated interval funds) as the localized tail risk.** Useful narrative color for the AM summary; not a quantitative feed.

---

## 2. What the dashboard already covers well

| Concept | Tool / field | Current read (2026-07-10) |
|---|---|---|
| Index VRP | `get_vix.vrp` | +5.6 (vega_zone "low") |
| Per-name IV vs forecast RV, richness | `get_vol_richness` (iv_atm, fcst_rv, vrp_pts, vrp_pctile, label) | 125 names; SPY/QQQ flagged "rich" |
| VIX complex + term structure | `get_vix` (vix, vvix, vix9d, vix3m, vix6m, term_9d_3m) | VIX 15.84, VVIX 87.3, 9d–3m −7.4 (steep contango) |
| Credit spreads | `get_vix` (hy_oas, ig_oas) | HY 2.70, IG 0.76 |
| Skew | `get_index_skew`, `get_skew_history`, `skew_25d` | per-name 25Δ skew present |
| **Dispersion / implied corr** | `index_skew_daily` cols `cor1m/cor3m/vixeq/dspx/vixeq_vix_spread` + pctiles; EOD COR1M Map; page `8_VIX`; `eod_narrative.dispersion_phrase` | **already built** (migs 0025/0027); now also via `get_index_skew` MCP |
| **Shift vs slide (VIX decomp)** | `greeks/vix_decomposition.py`; page `18_Vol_Regime` (5-dim decomp); `surface_changes.py` | **already built** — sticky-strike vs parallel-shift is covered |
| Forward/constant-maturity IV | `get_iv_tenor` | QQQ/SPY/SPX ATM+15/25Δ, 1M/3M |
| Gamma regime / walls / flip | `get_live_gex`, `get_walls`, `get_gamma_history` | matches Doc's Pillar-1/6 language |
| VIX options | `get_vix_options` | — |
| Newsletter KB (graphify) | `search_knowledge` | ~10 Doc McGraw posts ingested |

**Takeaway:** the *index-level* and *per-name* vol plumbing is genuinely strong and already speaks the newsletters' language (VIX9D/VVIX/contango/skew/GEX). The gaps are all in the **cross-sectional (dispersion)** and **time-window mechanical** dimensions.

---

## 3. Gaps & rule-compliant recommendations

Ordered by value. All framed as **regime descriptors**, not alerts — per the FlashAlpha rule (only validated `strategies/` scanners + the probability model write to `signals`). Data additions go through the `OptionsDataSource` Protocol (rule 1) + Alembic (rule 3).

### A. Dispersion / implied-correlation — ALREADY BUILT; MCP exposure DONE
- **Correction:** this stack already exists (Yahoo `fetch_cor1m/cor3m/vixeq/dspx` in `clients/prices.py` → `index_skew_daily` cols, migs 0025/0027 → `index_skew` job → EOD COR1M Map + page `8_VIX` + `dispersion_phrase`). No new table/vendor work needed.
- **Done today:** extended `get_index_skew` (mcp/extra_tools.py) to emit `cor1m, cor1m_pctile_252d, cor3m, cor3m_pctile_252d, cor_slope (=COR1M−COR3M), vixeq, vixeq_pctile_252d, dspx, dspx_pctile_252d, vixeq_vix_spread` — so Claude Desktop can read dispersion conversationally. Test extended in `tests/mcp/test_extra_tools.py`. Descriptor only (structure, not direction).
- **Optional follow-ups (not built):** a one-line "loaded spring" regime label on the watchlist/AM summary when COR1M pctile is crushed + `vixeq_vix_spread` extreme; and the dispersion-unwind composite in §C.

### B. Realized-vol window roll-off — BUILT (core transform)
- **Done today:** `prices/realized_vol.py::rv_rolloff_projection(close, window=21, horizon=10, future_return=0.0)` — holds the window width fixed and rolls it forward N sessions, dropping the oldest past return each day and appending a calm-tape return, so measured RV drifts down as big days age out. Returns a DataFrame (`session_offset`, `projected_rv`, `dropped_return`). Pure transform, tests in `tests/prices/test_realized_vol.py`. Doc's "~13 floor by July 13, then launchpad" mechanic.
- **Remaining:** wire it to an MCP tool (`get_rv_rolloff`) and/or an AM-summary bullet + a `8_VIX`/`18_Vol_Regime` panel. Feeds the "systematic vol-target buying keys off falling RV" read (Pillar 2).

### C. Dispersion-unwind composite descriptor (candidate for a validated scanner)
- Condition: **SPX down *and* VIX up *simultaneously*, with COR1M/implied-corr rising.** This is a *composite* condition (not a raw-Greek crossing), so it's FlashAlpha-compatible **as a `strategies/` scanner** once backtested — not a raw-GEX/DEX alert. Would need validation before it writes to `signals`.

### D. VIX decomposition (shift vs slide) — ALREADY BUILT
- **Correction:** `greeks/vix_decomposition.py` + `greeks/surface_changes.py` already decompose the surface (sticky-strike vs parallel-shift vs convexity), surfaced on page `18_Vol_Regime` (5-dimension decomposition) and page `8_VIX`. No build needed. Only possible add: a plain-English "mechanical vs regime" one-liner on the AM summary if not already there.

### E. Vol-regime persistence descriptor
- Encode Jared's asymmetry: low-vol clusters persist and mean-revert *slowly to the downside*; elevated vol's next move skews *up*. A simple persistence/clustering descriptor (e.g., conditional on current vega_zone) beats treating VIX as memoryless. Descriptor only.

### F. Expiration-mechanics overlay
- Mark monthly OPEX and VIX-expiration dates on the GEX/vol timeline with a note that dealer hedging (shock absorbers) thins post-OPEX. Complements existing gamma tooling.

---

## 4. Cross-check notes / things to verify in our data

- **VRP sign convention** — reconcile newsletter `IV−RV` (Jared's −5) with our `get_vix.vrp` (+5.6) and `vol_richness.vrp_pts`. Label the sign on the UI.
- **Index ETFs excluded from per-strike/skew** (MEMORY: `CHAIN_EXCLUDE_ROOTS`) — a dispersion proxy needs *index* IV; confirm we still capture SPY/SPX ATM IV via `get_iv_tenor`/`get_vol_richness` (we do: SPY iv_atm 0.148, QQQ 0.227 on 07-10).
- **Skew backfill** (MEMORY: `skew-not-collected`) — the dispersion/skew narrative leans on per-name skew history; the 16:55 skew backfill DSM task is worth prioritizing so these panels have depth.

## 5. Knowledge-base (graphify) ingestion gap

`search_knowledge` confirms the KB has the older evergreen Doc McGraw posts (VIX Decomposition, Shift vs Slide, Gamma Regime Fallacy, WTF Market, 12–2 Scalp, Why VIX Can Rise Without Panic, Earnings/Mobile dispersion, Sell-in-May dispersion, Doc's Mailbox/Inbox). **Not yet ingested** (recommend adding — evergreen, methodology-grade):

- **Doc — "The 6 Pillars of the Tape"** (May 25) — the structural framework; high reference value.
- **Doc — "The Market Is Near All-Time Highs / Why Are the Pros Bracing?"** (Jul 11) — the dispersion base-rate study + catalyst-calendar mechanics.
- **Doc — "Anatomy of a VIX Trade"** (Jan 10) — VIX basis + BWB structure (free educational).
- **Doc — "The Tao of the Tape"** (May 10) — macro backdrop (optional; narrative, not quant).
- **jaredhstocks — "Market Update and Volatility Clustering"** (Jun 30) — clustering + VRP + dispersion; **note this is a second author not currently in the pipeline** — worth deciding whether to add jaredhstocks as a tracked source.

Daily-plan emails (e.g., Jun 4, Feb 25) are time-dated tactical notes — probably skip for the KB, though their *volatility & positioning* sections echo the durable concepts above.

---

*Method note: full plaintext bodies of all 9 emails were read (HTML newsletters, parsed to plaintext). Dashboard readings pulled live from the trading-intel MCP (`get_vix`, `get_vol_richness`, `search_knowledge`) on 2026-07-11.*
