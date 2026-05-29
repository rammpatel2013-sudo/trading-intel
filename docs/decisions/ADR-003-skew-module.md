# ADR-003 — First-class Skew module (per-name skew + SKEWDEX + VIX-options + VIX-beta)

**Status:** Proposed (revision 2)
**Date:** 2026-05-28
**Author:** Mithil (with AI assist)
**Supersedes / relates to:** ADR-001 (collector split), `vol/richness.py`, `vol/term_skew.py`, `greeks/surface.py`, `clients/cboe.py` (VVIX).
**Change vs revision 1:** Drops the FlashAlpha rule-4 descriptor-only restriction for skew. The module is signal-eligible. Adds (a) ingestion of Cboe SKEW / Nations SkewDex® (SDEX) tickers, (b) ingestion of the VIX options chain via ConvexValue, (c) per-name VIX-beta as a normalization layer.

---

## 1. Context

Skew is touched in three existing places — `greeks/surface.DeltaSurface` (the |Δ| grid), `vol/term_skew.py` (single 25Δ label), `vol_richness` table (one column, 30/60d). What's missing — and what the MU "1.61 % 3-month skew percentile" chart and the SPX-style time-series chart make obvious — is a **dedicated skew layer** with three new ingredients vs revision 1:

1. **Two index-level skew tickers stored alongside VIX/VVIX:** the Cboe SKEW Index (third-moment estimator over OTM SPX options) and the Nations **SkewDex®** family (notably **SDEX**, Large-Cap), which is a cleaner ATM-vs-1σ-OTM-put skew measure designed to be tradable and intraday-disseminated ([Nations SkewDex Fact Sheet](https://nations.com/wp-content/uploads/2024/01/Nations-SkewDex-Index-Fact-Sheet.pdf)). The two are complementary — Cboe SKEW captures the full third moment of SPX, SDEX captures the practitioner ATM-anchored read — and using both costs almost nothing.
2. **VIX options chain via ConvexValue.** VIX options carry a structural **call skew** (the opposite of equity put skew) because tail-risk hedgers bid up OTM VIX calls. The shape of the VIX call wing and its OI distribution is a direct read on institutional tail-hedging demand — historically a leading indicator of stress regimes alongside VVIX/VIX ([Cboe VIX Tail Hedge methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_VIX_Tail_Hedge_Index_Methodology.pdf), [HL Hunt — Tail Risk Hedging](https://www.hlhunt.org/uncategorized/tail-risk-hedging-advanced-options-strategies-for-portfolio-protection-hl-hunt-financial/)).
3. **VIX-beta per name.** For each watchlist name we regress 60-day daily ATM IV changes on contemporaneous VIX changes (`Δivᵢ,t = α + β·ΔVIXt + εt`). The resulting `vix_beta` is the natural scale for normalizing a single-name skew read against the index — a high-beta-to-VIX name will show wider RR moves for the same change in market skew, so the *abnormal* skew shift is `Δrr_25dᵢ,t − β·ΔSDEXt`. MenthorQ's Understanding-Beta guide describes exactly this VIX×β approach ([MenthorQ — Understanding Beta](https://menthorq.com/guide/understanding-beta/), [MenthorQ — Vega Notional](https://menthorq.com/guide/why-is-vega-notional-important/)).

These three additions move the layer from "richer regime descriptor" (revision 1) to **signal-eligible**.

### Decision to drop the FlashAlpha rule-4 gating for skew

Revision 1 forced skew to be a regime descriptor only. The FlashAlpha rule (CLAUDE.md rule 4) was written specifically about **GEX / DEX / VEX / CHEX**, where the underlying backtest research showed no single Greek exposure has predictive edge once ATM IV is controlled for. That research did NOT cover skew. The opposite is true in the published equity-options literature:

- [Xing / Zhang / Zhao (JFQA 2010)](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/ECFD16BA9ACBDC8D577D1BD866FBEA72/S0022109010000220a.pdf/what-does-the-individual-option-volatility-smirk-tell-us-about-future-equity-returns.pdf) — the steepest-smirk decile underperforms the flattest by ~10.9 %/yr risk-adjusted, holding for up to 6 months, concentrated around earnings.
- [Doran / Krieger / Peterson — Volatility Skew, Earnings Announcements, and the Predictability of Crashes](https://www.researchgate.net/publication/228204703_Volatility_Skew_Earnings_Announcements_and_the_Predictability_of_Crashes) — skew predicts post-earnings drift and crashes specifically.

Skew earns signal status. The module therefore writes to **both** `skew_snapshots` (the descriptor table for the dashboard and AM summary) and, through a sibling `strategies/skew.py`, to the `signals` table — keeping CLAUDE.md rule 4's *architectural* constraint intact (only `strategies/` modules write to `signals`).

## 2. Decision

A **descriptor layer** in `vol/` plus a **signal layer** in `strategies/`, with their data scoped through `OptionsDataSource` so a vendor swap is still cheap.

### 2.1 New / modified clients

| File | Change |
|---|---|
| `clients/__init__.py` (`OptionsDataSource` Protocol) | Add `vix_chain(*, exps=(1,2,3)) -> DataFrame` returning the VIX options chain with the same normalized columns as `chain()` (strike, expiration, opt_kind, delta, gamma, iv, oi, oi_change, volume, gxoi, vxoi). |
| `clients/convex.py` | Implement `vix_chain` — empirically Convex's `get_chain_as_rows("_VIX", …)` returns the index option chain; if the underscore-prefixed symbol path doesn't work we fall back to `^VIX` / `VIX`. Smoke test before merge. |
| `clients/cboe.py` | Already has VVIX. Add `skew_index() -> float` (Cboe SKEW close) and `sdex() -> float` (Nations SDEX close) using the same minimal-quote pattern as `vvix()`. SDEX availability is via Bank of America (Nations licensee); if not directly quotable, fall back to a Tradier/Polygon snapshot — captured as a vendor decision in section 7. |

The Protocol additions are mandatory for rule 1; both real `ConvexClient` / `CboeClient` and the test mock get implementations.

### 2.2 New pure modules

```
trading_intel/vol/skew.py                 -- per-name skew computation (RR, BF, term)
trading_intel/vol/vix_skew.py             -- VIX-options skew + OI distribution analytics
trading_intel/vol/vix_beta.py             -- rolling OLS of Δiv_atm on ΔVIX (per name)
trading_intel/vol/skew_descriptors.py     -- the row builder (~ vol/richness row builder)
```

`vol/skew.py` — pure functions over a `DeltaSurface`:

- `risk_reversal(surface, delta, expiry_idx)` → `iv_put_Δ − iv_call_Δ` (vol points).
- `butterfly(surface, delta, expiry_idx)` → `(iv_put_Δ + iv_call_Δ)/2 − iv_atm`.
- `skew_term_curve(surface, delta) -> list[tuple[dte, rr]]`.
- `front_back_slope(surface, delta, near_h, far_h)` → front-month RR minus back-end RR.
- `skew_percentile(history, current, *, min_history=20)` → unchanged from richness percentile shape; computed over 63d AND 252d windows.
- `classify_rr(rr_pts)` → `"steep put bid"` / `"moderate put bid"` / `"flat"` / `"inverted call bid"` / `"extreme call bid"`. Thresholds in vol points reuse `vol.term_skew` constants.
- `extreme_label(pctile, *, hi=0.95, lo=0.05)` → flag tail readings (the MU 1.61 % pattern).
- `shift_vs_slide(prev_row, today_row)` → label today's move as `shift-dominated` (ATM IV moved, RR didn't) vs `slide-dominated` (ATM IV unchanged, RR moved) vs `mixed`. Per `docs/playbooks/from-doc-s-mailbox-volatility-shift-vs-slide-what-s-the-difference.md`.

`vol/vix_skew.py` — VIX-options analytics (positive structural skew, opposite sign convention is OK as long as it's documented):

- `vix_call_wing_iv(chain, *, delta=0.25, expiry_idx=0)` → 25Δ VIX call IV.
- `vix_call_skew(chain, *, delta=0.25, expiry_idx=0)` → `iv_call_Δ − iv_atm` (positive = call wing rich).
- `vix_term_call_skew(chain, deltas)` → call-wing IV across the nearest 3 expiries.
- `vix_call_oi_share(chain, *, strike_floor)` → fraction of OI in OTM calls vs total — institutional hedging proxy.
- `vix_call_premium_share(chain, ...)` → notional-weighted version of the above.

`vol/vix_beta.py` — single function:

- `vix_beta(iv_history: pd.Series, vix_history: pd.Series, *, window=60, min_obs=40) -> float | None`. Daily-frequency OLS of `Δiv` on `ΔVIX`, both in vol points. Returns `None` if insufficient history.

`vol/skew_descriptors.py` — row builder mirroring `vol/richness.build_richness_row`.

### 2.3 New schema

`alembic/versions/0019_skew_snapshots.py` — per-name table (rule 3 — reversible):

```
skew_snapshots(
  id, symbol, ts, horizon_dte INT,
  atm_iv FLOAT,
  rr_10d FLOAT,   rr_25d FLOAT,
  bf_10d FLOAT,   bf_25d FLOAT,
  rr_25d_pctile_63d FLOAT,    rr_25d_pctile_252d FLOAT,
  bf_25d_pctile_252d FLOAT,
  front_back_rr_slope FLOAT,
  vix_beta_60d FLOAT,
  rr_25d_abnormal FLOAT,            -- Δrr - β·ΔSDEX, see §3.3
  shift_slide_label VARCHAR(16),
  label VARCHAR(64),
  UNIQUE(symbol, ts, horizon_dte),
  INDEX(symbol, ts)
)
```

`alembic/versions/0020_index_skew_daily.py` — index-level table:

```
index_skew_daily(
  date PK,
  cboe_skew FLOAT,                  -- Cboe SKEW close
  sdex FLOAT,                       -- Nations SkewDex close
  spx_rr_25d_30d FLOAT,             -- our computed SPX 25Δ RR at 30d
  spx_rr_pctile_252d FLOAT,
  sdex_pctile_252d FLOAT,
  vvix FLOAT,                       -- already in vix_data but mirrored for self-contained queries
  vix_call_skew_25d FLOAT,          -- VIX options 25Δ call wing minus ATM
  vix_call_oi_share FLOAT,          -- OTM call OI / total OI
  vix_tail_hedging_score FLOAT      -- §3.4
)
```

Both tables are **un-pruned** (the trailing distribution IS the percentile baseline; `vol_richness` already follows this convention).

### 2.4 New scheduler jobs

```
scheduler/jobs/skew_snapshots.py    -- EOD, after oi_chain_eod, before am_summary
scheduler/jobs/vix_options.py       -- EOD, calls OptionsDataSource.vix_chain() → vix_options_chain table
scheduler/jobs/index_skew.py        -- EOD, pulls Cboe SKEW + SDEX from CboeClient → index_skew_daily
```

All three idempotent (`ON CONFLICT DO UPDATE`), all three registered in `scheduler/runner.py` AND added as DSM Task Scheduler entries on the NAS (per CLAUDE.md NAS deployment rule).

### 2.5 The signal layer

```
trading_intel/strategies/skew.py
```

Implements the `SignalGenerator` Protocol (`__call__(session) -> list[Signal]`). Initial signal set, each gated by an explicit threshold and the existing VEGA/VIX regime gate:

| Signal | Trigger | Rationale |
|---|---|---|
| `SKEW_EXTREME_CALL_BIAS` | `rr_25d_pctile_252d ≤ 0.02` AND `bf_25d_pctile_252d ≤ 0.20` (calls cheap, wings not blown out) | The MU pattern — extreme call bias with reasonable wings = directional speculation in single names. Xing-Zhang-Zhao predicts negative drift; we surface both long-call-wing structures AND short-stock as raw signal *candidates*. The trader chooses. |
| `SKEW_TAIL_PUT_BID` | `rr_25d_pctile_252d ≥ 0.98` AND VIX zone in `low`/`mid` | Pre-event hedging spike; mean-reversion candidate (sell the put wing) OR confirmation of bearish news flow. |
| `SKEW_SHIFT_VS_SPX` | `rr_25d_abnormal` two-tail z-score `≥ 2.5` (i.e., name moved differently from what its VIX-beta predicts) | Idiosyncratic skew dislocation. Strongest read when index skew is stable and a single name dislodges. |
| `VIX_TAIL_HEDGING_SPIKE` | `vix_tail_hedging_score` percentile `≥ 0.95` over 252d AND VVIX/VIX ≥ 1.2× its 90d mean | Institutions are bidding the VIX call wing. Regime signal — surfaced market-wide on the dashboard and into AM summary; NOT a per-name signal. |
| `INDEX_SKEW_REGIME_FLIP` | SDEX 5d momentum > 95th pctile (rising) AND Cboe SKEW > 145 | Broad-market de-risking phase; reduces position sizing for long-equity signals downstream (consumed by the probability model in Phase 5+). |

Each signal carries its trigger inputs in the `Signal.context` dict so the AM summary can render *why*. CLAUDE.md rule 4 (architecturally only `strategies/` writes to `signals`) is preserved — what's lifted is the descriptive-only restriction on skew.

Backtest gate: before any signal is enabled in production, a Phase-5-style backtest under `research/backtests/skew/` must show meaningful edge across `(2018-2026)` data. Until then signals are flagged `experimental=True` and held back from Discord alerts but visible on the dashboard.

### 2.6 Dashboard

`trading_intel/dashboard/pages/17_Skew.py` — three tabs:

1. **Per-name view** — reproduces the MU reference image: candles + trailing RR band + skew percentile chip + a "shift-dominated / slide-dominated" badge for the latest session. Source: `skew_snapshots`.
2. **Index time series** — reproduces the first reference image: 10Δ Call / 25Δ Call / 10Δ Put / 25Δ Put / 25Δ RR over user-selected lookback (90/180/365/730d) and maturity (30/60/90/180d). Source: `skew_snapshots` aggregated to index proxies (SPY, QQQ, IWM) + `index_skew_daily` overlay.
3. **VIX-options view** — VIX call-wing IV by strike, top-of-book OI distribution, the `vix_tail_hedging_score` and its history. Source: `vix_options_chain` and `index_skew_daily`.

### 2.7 AM summary integration

`synthesis/am_summary.py` gains a `skew_block` in its facts dict:

```
{
  "index": {
    "cboe_skew": ..., "cboe_skew_pctile_252d": ...,
    "sdex": ..., "sdex_pctile_252d": ...,
    "spx_rr_25d_30d": ..., "spx_rr_pctile_252d": ...,
    "vix_tail_hedging_score_pctile_252d": ...
  },
  "single_name_extremes": [ {symbol, rr_25d, rr_pctile_252d, label}, ... ],
  "signals_today": [ ... ]    # from strategies/skew
}
```

Prompt block written so Claude reports *facts and the named signals* — no synthesized opinions about direction beyond what the signal already encodes. Sonnet, not Opus (CLAUDE.md rule 7).

## 3. Methodology notes

### 3.1 Per-name skew points + percentile

25Δ as the institutional convention ([Fly On The Wall](https://flyonthewall.ai/25-delta-risk-reversal/), [MenthorQ — Risk Reversal and SKEW Guide](https://menthorq.com/guide/risk-reversal-and-skew/)); 10Δ as the tail. The percentile rank vs the name's own trailing distribution (63d short / 252d long) is the only thing comparable across names — MenthorQ's product uses exactly the 3-month percentile ([MenthorQ — Skew Guide](https://menthorq.com/guide/menthor-q-skew/)).

### 3.2 SKEWDEX vs CBOE SKEW

The Nations **SkewDex®** (ticker SDEX for Large-Cap) is more direct than Cboe SKEW: it compares the IV of a precisely ATM 30d SPY option to the IV of a 1-stdev-OTM-put 30d SPY option, with both moneyness and maturity standardized intraday every 15 seconds ([Nations Fact Sheet](https://nations.com/wp-content/uploads/2024/01/Nations-SkewDex-Index-Fact-Sheet.pdf)). Cboe SKEW captures the third moment via the Bakshi/Kapadia/Madan model-free framework but suffers from significant statistical noise and only updates EOD ([Zhen — A Theory of the CBOE SKEW](https://acfr.aut.ac.nz/__data/assets/pdf_file/0003/56406/36753-F-Zhen-CBOE_SKEW.pdf), [Cboe SKEW Dashboard](https://www.cboe.com/us/indices/dashboard/skew/)). We store both and use SDEX as the primary input to signal triggers; Cboe SKEW as a cross-check on tail-of-distribution regimes.

### 3.3 VIX-beta normalization

For each watchlist name, run a 60-day OLS:

```
Δ iv_atm_30d_i,t = α_i + β_i · Δ VIX_t + ε_i,t
```

`β_i` is the **VIX beta** for name `i`. Then the abnormal RR change relative to the index:

```
rr_25d_abnormal_i,t = Δ rr_25d_i,t − β_i · Δ sdex_t
```

A name with `vix_beta = 1.5` should see its RR move ~50 % more than the index on any given session; the abnormal residual is the part NOT explained by that. Two-tail z-scores on the abnormal series are how `SKEW_SHIFT_VS_SPX` fires. This is the same beta-adjusted-vega logic MenthorQ describes; we just apply it to skew rather than to vega notional.

### 3.4 VIX call-wing tail-hedging score

A composite read on demand for systemic-tail insurance:

```
vix_tail_hedging_score
  = z(vix_call_skew_25d, 252d)
  + z(vix_call_oi_share, 252d)
  + z(vvix_vix_ratio, 252d)
```

The three sub-components are uncorrelated enough that summing standardized z-scores gives a balanced index ([Cboe VIX Tail Hedge methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_VIX_Tail_Hedge_Index_Methodology.pdf) uses a similar OTM-VIX-call structure for index construction). Stored as a column on `index_skew_daily` for fast queries.

### 3.5 Shift vs Slide labeling

Per the project's existing playbook, decompose each day's per-name move:

- If `|Δ atm_iv| > τ_atm` and `|Δ rr_25d| < τ_rr` → **shift-dominated** (level move, skew steady).
- If `|Δ atm_iv| < τ_atm` and `|Δ rr_25d| > τ_rr` → **slide-dominated** (level steady, skew migrated).
- Otherwise mixed.

`τ_atm = 0.005` (50 bps of vol), `τ_rr = 0.5` vol pts to start; review after first month of live data.

## 4. Rule-by-rule compliance check (CLAUDE.md, with the rule-4 relaxation flagged)

| Rule | How this plan complies |
|---|---|
| 1 — Data isolation | `vix_chain` added to `OptionsDataSource` Protocol; Cboe SKEW + SDEX through `CboeClient`. No direct `convexlib` outside `clients/`. |
| 2 — Secrets | None new. |
| 3 — Alembic | Two new reversible migrations (`0019`, `0020`); also a `0021_vix_options_chain.py` for the VIX chain snapshot. |
| 4 — FlashAlpha | **Relaxed for skew.** Justification documented in §1. Rule's architectural constraint preserved: only `strategies/skew.py` writes to `signals`. Other Greeks remain descriptor-only. CLAUDE.md to be updated by the same PR with a footnote: "Rule 4 applies to GEX/DEX/VEX/CHEX exposures; skew is signal-eligible per ADR-003." |
| 5 — Idempotency | All three jobs upsert on natural keys. |
| 6 — Tests | `tests/vol/test_skew.py`, `tests/vol/test_vix_skew.py`, `tests/vol/test_vix_beta.py`, `tests/scheduler/test_skew_snapshots.py`, `tests/scheduler/test_vix_options.py`, `tests/strategies/test_skew.py`. Golden values pinned to synthetic surfaces. |
| 7 — Cost-aware Claude | No new Claude calls. AM summary tokens grow linearly with the new facts block. |

## 5. File-by-file deliverables

```
clients/__init__.py                                      (+ vix_chain on Protocol)
clients/convex.py                                        (+ vix_chain implementation)
clients/cboe.py                                          (+ skew_index, sdex)
vol/skew.py                                              (pure RR/BF/term-curve/percentile/shift-slide)
vol/vix_skew.py                                          (VIX call wing + OI distribution)
vol/vix_beta.py                                          (rolling 60d OLS Δiv on ΔVIX)
vol/skew_descriptors.py                                  (row builder)
memory/models.py                                         (+ SkewSnapshot, IndexSkewDaily, VixOptionsChain ORM)
alembic/versions/0019_skew_snapshots.py
alembic/versions/0020_index_skew_daily.py
alembic/versions/0021_vix_options_chain.py
scheduler/jobs/skew_snapshots.py
scheduler/jobs/vix_options.py
scheduler/jobs/index_skew.py
scheduler/runner.py                                      (+ 3 cron registrations)
strategies/skew.py                                       (5 SignalGenerators above)
synthesis/am_summary.py                                  (+ skew_block in facts dict)
synthesis/prompts.py                                     (+ skew section in the AM prompt template)
dashboard/skew_data.py                                   (page data loaders)
dashboard/pages/17_Skew.py                               (3-tab Streamlit page)
tests/vol/test_skew.py
tests/vol/test_vix_skew.py
tests/vol/test_vix_beta.py
tests/scheduler/test_skew_snapshots.py
tests/scheduler/test_vix_options.py
tests/scheduler/test_index_skew.py
tests/strategies/test_skew.py
docs/playbooks/skew-module.md                            (one-pager — reading the page)
research/backtests/skew/                                 (Phase-5-style notebooks per signal)
MEMORY.md                                                (+ row in page table, + section on signal list)
DEPLOYMENT.md                                            (+ 3 NAS DSM tasks)
CLAUDE.md                                                (rule-4 footnote referencing ADR-003)
```

## 6. Implementation order

1. **Day 1 — schema + per-name pure functions.** Migrations 0019/0020/0021; `vol/skew.py`; tests.
2. **Day 2 — VIX-options + VIX-beta.** `vol/vix_skew.py`, `vol/vix_beta.py`, `clients/convex.py vix_chain` (with live smoke test), tests.
3. **Day 3 — index skew + jobs.** `clients/cboe.py` SDEX + SKEW; three scheduler jobs; backfill flags.
4. **Day 4 — strategies.** `strategies/skew.py` with `experimental=True` flag on all signals; integration tests.
5. **Day 5 — dashboard + AM summary.** Streamlit page 17; AM prompt and prompt-block tests.
6. **Day 6 — backtests.** Notebooks under `research/backtests/skew/`. Promote signals out of `experimental` only on a green backtest.
7. **Day 7 — NAS deploy.** Image rebuild, three new DSM tasks, smoke test on a paper symbol.

Total: ~one week.

## 7. Open questions

1. **SDEX licensing / quote path.** Nations SkewDex is a registered index; intraday quote redistribution may require a license. Confirm via the CBOE delayed feed, Bank of America data, or via Tradier/Polygon. Worst case we proxy SDEX with our own `iv_atm_30d_SPY − iv_put_1σ_OTM_30d_SPY` — that's literally what the index measures.
2. **VIX chain on Convex.** Need to confirm the exact symbol Convex's `get_chain_as_rows` accepts for VIX options (`VIX`, `^VIX`, `_VIX`, or `$VIX`). Smoke test in development before wiring the job.
3. **Backfill depth.** `oi_chain_eod` retains ~6 months — the 252d percentile cold-starts in late 2026. Acceptable, but call it out in the dashboard with a "warming" badge until full history accrues.
4. **Signal-threshold tuning.** The 0.02 / 0.98 percentile cutoffs are reasonable defaults; the backtest will set the production thresholds.
5. **Confirm horizons:** `30 / 60 / 90 / 180 / 365` calendar days for the term-structure of skew.

## 8. Out of scope (separate ADRs)

- Intraday skew tracker.
- SVI / SSVI parameterization (only matters when we start pricing exotics).
- Cross-asset skew (FX / commodities).
- A cross-name skew dispersion factor (worth doing but a different scope).

---

## Sources

- [Nations SkewDex® Fact Sheet](https://nations.com/wp-content/uploads/2024/01/Nations-SkewDex-Index-Fact-Sheet.pdf)
- [Cboe — SKEW Index Dashboard](https://www.cboe.com/us/indices/dashboard/skew/)
- [Cboe — Dawn of a New Era Brings on the Existence of Skew](https://www.cboe.com/insights/posts/dawn-of-a-new-era-brings-on-the-existence-of-skew/)
- [Cboe — VIX Tail Hedge Index Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_VIX_Tail_Hedge_Index_Methodology.pdf)
- [Zhen — A Theory of the CBOE SKEW (BKM third-moment framework)](https://acfr.aut.ac.nz/__data/assets/pdf_file/0003/56406/36753-F-Zhen-CBOE_SKEW.pdf)
- [MenthorQ — Understanding Beta](https://menthorq.com/guide/understanding-beta/)
- [MenthorQ — Risk Reversal and SKEW Guide](https://menthorq.com/guide/risk-reversal-and-skew/)
- [MenthorQ — Skew Guide](https://menthorq.com/guide/menthor-q-skew/)
- [MenthorQ — Why Vega Notional Matters](https://menthorq.com/guide/why-is-vega-notional-important/)
- [Fly On The Wall — 25-Delta Risk Reversal](https://flyonthewall.ai/25-delta-risk-reversal/)
- [Xing / Zhang / Zhao — What Does Individual Option Volatility Smirk Tell Us About Future Equity Returns? (JFQA)](https://www.cambridge.org/core/services/aop-cambridge-core/content/view/ECFD16BA9ACBDC8D577D1BD866FBEA72/S0022109010000220a.pdf/what-does-the-individual-option-volatility-smirk-tell-us-about-future-equity-returns.pdf) ([SSRN](https://dx.doi.org/10.2139/ssrn.1107464))
- [Doran / Krieger / Peterson — Volatility Skew, Earnings Announcements, and the Predictability of Crashes](https://www.researchgate.net/publication/228204703_Volatility_Skew_Earnings_Announcements_and_the_Predictability_of_Crashes)
- [HL Hunt — Tail Risk Hedging: Advanced Options Strategies](https://www.hlhunt.org/uncategorized/tail-risk-hedging-advanced-options-strategies-for-portfolio-protection-hl-hunt-financial/)
- [Cboe — VIX Decomposition (2025-08-01)](https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf)
- Project sources: `docs/guides/reading-the-vol-surface.md`; `docs/playbooks/from-doc-s-mailbox-volatility-shift-vs-slide-what-s-the-difference.md`; `docs/playbooks/the-algo-overlords-are-buying-insurance-that-tells-you-something.md`; `docs/playbooks/riding-on-a-smile.md`; `docs/playbooks/santander-volatility-trading-primer-part-i-1.md`; `docs/playbooks/trading-volatility.md`; `docs/playbooks/forecasting-implied-volatility-surface-dynamics-of-equity-options.md`.
