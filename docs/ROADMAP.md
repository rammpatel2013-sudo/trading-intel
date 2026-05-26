# trading-intel — Build Roadmap

_Dated 2026-05-22. Companion to MASTER_PLAN.md — maps the 7-phase plan against what is actually built and running now, with dependencies, data-accrual gates, and rough effort (S/M/L)._

## Where we are now

- **Phase 0 (foundation)** and **Phase 1 (Convex client + Greeks ingestion): DONE.**
- **Daily collection is LIVE** on the Synology NAS — `chain_snapshot` (per-strike chain), `greeks_snapshot` (aggregate GEX/DEX/VEX/CHEX), `gex_rolling` (6-month rolling GEX), running 17:00 NAS = 6 PM ET against a Postgres container. History accruing from **2026-05-22**.
- Built this session beyond Phase 1: options flow + multi-leg package detection, IV surface (delta + moneyness + term/forward vol), surface read-through report, **fixed-strike & ATM day-over-day change panels**, **call/put wall tracker**.
- ~101 tests passing.

## Not yet built

- **Phase 2** Streamlit dashboard pages — the hosted app is still a scaffold; rich output lives only in the vol-surface HTML script + CLI.
- **Phase 3** macro theme KB — `pdf_pipeline.py` / `tagging.py` scaffolded, not wired/active.
- **Phases 4–6** — strategy ports/alerts, AM summary + anomaly detection, earnings ripple, GEX-VEX heatmap: not started.
- **Phase 7** hosting — collection is on the NAS; the dashboard itself is not hosted yet.

## Roadmap (ordered, by track)

### Track A — Make it visible (highest near-term value; data ready)
- **A1. Per-ticker dashboard page** — ✅ DONE 2026-05-22 (`pages/1_Ticker.py`): price + SMA + Bollinger + GEX overlay, GEX/DEX-by-strike (+rolling avg/normal fit, flip & spot marks), RSI, walls, change panels. Plus: intraday 0DTE/1DTE volume-weighted gamma/vanna/charm page (`pages/2_Intraday_0DTE.py`, 5-min collector) and daily price-history backfill (`quotes_daily`, rv20/60). (Phase 2)
- **A2. GEX-VEX heat map** — 2D dealer-flow surface (spot% × IV-shock), flip boundary. Dep: chain+surface (have). Gate: none. Effort: **M**. (Phase 6 item, data-ready now)
- **A3. Quick reads** — wall-vs-spot distance, GEX flip vs spot, biggest fixed-strike movers. Dep: data (have). Gate: none. Effort: **S** each.

### Track B — Vol-spike early warning
- **B1. VIX / VVIX + term-structure dashboard (#23)** — Convex OI + CBOE futures term structure. Gate: none. Effort: **M**.
- **B2. Thrasher dispersion recalibration** — re-fit VIX/VVIX SD thresholds on 2020–2025. Dep: historical VIX/VVIX. Gate: none (historical, not accrual). Effort: **M**.
- **B3. 5-condition confluence vol-spike model** — Thrasher + VIX zone + credit spreads (FRED) + GEX:RVOL decline + spot-up/vol-up. Dep: B1+B2. Gate: needs ~2–3 wks of GEX:RVOL history. Effort: **L**.

### Track C — Intelligence layer (mostly data-gated)
- **C1. AM-summary generator (7 AM, Claude)** — synthesize the morning read. Dep: feeds (have most). Gate: improves as data accrues; buildable now. Effort: **M**. (Phase 5)
- **C2. Anomaly detection** — spot-up/vol-up, GEX flip cross, DEX strike migration, fixed-strike repricing, QOPEX. Dep: C1 + change panels. Gate: needs daily history. Effort: **M–L**.
- **C3. Macro theme KB** — PDF ingest → embed → tag → pgvector search. Dep: Ollama/Voyage embeddings. Gate: none (independent of market data). Effort: **L**. (Phase 3)
- **C4. Earnings ripple engine** — direct demand vs share-shift vs sentiment contagion. Dep: earnings data + C3. Gate: needs an earnings cycle. Effort: **L**. (Phase 6)
- **C5. Probability model** — combines Greeks + VIX + ATM IV + credit spreads (the only thing allowed to upgrade descriptors → signals). Dep: most of the above. **HARD GATE: 4–8 weeks of accrued/tagged data.** Effort: **L**. _Do not start before ~July._

### Track D — Hosting / polish
- **D1. Host the Streamlit dashboard on the NAS** behind a reverse proxy, so you view it in a browser (not HTML files). Dep: A1. Effort: **M**.
- **D2. Optional 2nd morning collector run** for settled OI. Effort: **S**.

## Recommended order

1. **A1 per-ticker dashboard + A3 quick reads** — immediate payoff, uses data you already store.
2. **D1 host it on the NAS** — actually see it in a browser, every day.
3. **B1 VIX dashboard → B2 Thrasher recalibration.**
4. **C3 macro theme KB** in parallel (it's independent of market-data accrual).
5. Let data accrue ~4–8 weeks, then **C1/C2 AM summary + anomalies**, then **C5 probability model**.

## Data-accrual gates (time, not effort, is the blocker)

| What | Needs |
|---|---|
| Change panels / wall drift | 2 days (≈ tomorrow) |
| Sticky-strike regime, GEX:RVOL trends | ~2–3 weeks |
| Probability model (C5) | 4–8 weeks |

## Guardrail (unchanged)

FlashAlpha rule: GEX/DEX/VEX/CHEX, walls, and surface reads are **regime descriptors, not signals**. No alerts off raw Greek crossings until the probability model (C5) exists.


## Session 2026-05-22 — progress + next-up

**Shipped:** A1 per-ticker page; intraday 0DTE/1DTE volume-flow (table + 5-min collector + page); daily price history (yfinance `PriceDataSource`, rv20/60, backfill + EOD job). Migrations 0005 (intraday_flow) + 0006 (volume→bigint). Pending NAS redeploy to run the new jobs.

**Requested next (queued, with gates):**
- ✅ **Watchlist overview table/view** (DONE) — Streamlit page `pages/3_Watchlist.py` + HTML report `scripts/watchlist_report.py`. Metrics: per-ticker: total GEX (+ up/down), call/put ratio, vol/OI ratio, ΔtotalGEX over last week, Δcall wall/put wall, skew & its change. _Descriptive (rule 4). Structure buildable now; week-over-week cells gated on ≥1 week of history._
- **Better fixed-strike vol viz** — turn `build_change_report` markdown into charts; overlay vol change vs call-wall drift. _Buildable now; drift needs ≥2 days._
- ✅ **Major option-flow panel** (DONE) — `flow_snapshots` table (migration 0007), 30-min RTH collector `scheduler/jobs/flow_snapshot.py`, page `pages/4_Flow.py` (watchlist overview + per-symbol top prints & multi-leg packages).
- ✅ **Dynamic watchlist from uploaded research** (DONE) — `watchlist_entries` table (migration 0008); `synthesis/watchlist_extract.py` LLM extractor; `memory/watchlist_ingest.py` (`python -m ... <file>`); `pages/5_Research_Watchlist.py` (rationale/sentiment + regime metrics). Needs Ollama running to ingest.
- ✅ **Fixed-strike vol charts + Fibonacci** (DONE) — Ticker page: fib overlay (`prices/fibonacci.py`), fixed-strike ΔIV chart + call/put-wall drift chart (`load_fixed_strike_changes`, `wall_history_frame`).
- **Gamma-squeeze "will it happen" / pre-explosive-move read** — ⚠️ this is signal/prediction territory. Per FlashAlpha rule 4 + C5 gate, NO predictive alerts until the probability model (4–8 wks of data, ~July). Buildable now only as **descriptive ingredients**: short-dated gamma concentration, call-wall proximity to spot, vol/OI spikes, GEX flip vs spot — shown as a read-through, not a prediction.


## Session 2026-05-26 — agreed punch list (build / verify / deploy)

Confirmed with Mithil. Order = current build order. Everything descriptor-first (rule 4); promote to `strategies/` only after the backtest proves edge.

### To build

**Vol-richness scanner (main workstream — scoped 5/24; see MEMORY "Vol-richness scanner (PLANNED)" for full detail):**
1. `prices/forecast_vol.py` — HAR-RV (Corsi daily/weekly/monthly, OLS) + EWMA(0.94) baseline; per-symbol off `quotes_daily` close; annualized forward RV @30 / @60. (Yang-Zhang OHLC upgrade later.) + tests. **← IN PROGRESS**
2. `vol/richness.py` — ATM IV per horizon (from `surface.build_delta_surface().atm_iv`) − forecast RV → `vrp_pts`; standardized to the name's own VRP percentile + IV rank → ranking frame. + tests.
3. `vol/term_skew.py` — term slope (incl. 30↔60 calendar + `vix_data` term), 25Δ skew vs history, mandatory VEGA/VIX regime gate (short-vol OFF in stress >32). + tests.
4. Migration `0011_vol_richness` — `vol_richness` table, **UN-PRUNED** (doubles as the long IV/VRP percentile baseline). Reversible.
5. `scheduler/jobs/vol_richness.py` — EOD ~16:40 after `oi_chain_eod`, idempotent upsert, stored data only. + NAS DSM task on deploy.
6. `dashboard/vol_richness_data.py` + `dashboard/pages/9_Vol_Richness.py` — sortable rich/cheap sheet, both horizons, regime-gated, descriptive labels only.
7. **Vol cone / expected-move envelope** — ±1σ/±2σ bands in three flavors (implied / realized / forecast-RV) at weekly/monthly/quarterly; range-usage overlay + walls as markers.
8. `scripts/backtest_vol_richness.py` — validation gate (IV vs realized forward vol, variance-swap P&L proxy, vs naive always-short baseline + gate).
9. Convex: add `vomma` (+ `vommaxoi`/`vommaxvolm`) to `_CHAIN_PARAMS` behind the same graceful-fallback wrapper as `oi_ch`, surfaced through the Protocol.
10. AM-report wiring — top-3 richest/cheapest into `build_am_context`.

**Carry-over:**
- Wire the **methodology RAG into the AM report** (`render_am_markdown` → `retrieve_chunks(kind="methodology")` → inject into `AM_SUMMARY_PROMPT`) — deferred item-2 finish.

**Optional viz polish (not blocking):** treemap (type→expiry→strike, size=volume/color=OI), DAOI diverging-bar layout, expected-range band. (Rejected: directional-bias gauge as an interpretation — rule 4.)

### To verify
- ✅ **CBOE endpoints** — verified live 2026-05-26 (shape `{timestamp,data:{current_price},symbol}`; parser correct, no code change). Still: run `vix_snapshot` against the DB once to confirm a row writes (vega_zone, vvix_sd20).
- `oi_ch` Convex param — `c.probe_param('SPY','oi_ch')` before NAS deploy.
- `vomma` availability live — confirm it doesn't 400 the chain.
- Convex IP module — verify implied-probability/expected-move module detail (didn't render in the glossary) so the cone stays vendor-neutral.
- Data-gated pages after Tue 5/26 EOD — `scripts/verify_oi_flow.py` (≥2 `oi_chain_eod` snapshots, native `oi_ch` sign-agrees with our ΔOI, ΔGEX/mean-ΔIV roll-up, GEX surface 2nd column); eyeball page 7 (ΔIV + positioning) + GEX surface.
- Methodology embeddings — confirm 22-PDF ingest finished + `sync_knowledge --skip-research` backfilled.
- Set-aside scanned PDF in `research/doc/_skip` — decide OCR vs drop.

### To deploy / confirm on NAS (Mithil's machine)
- `chain_snapshot` / `greeks_snapshot` have **no DSM task** — add them (that's why the OI study + GEX surface accrue slowly).
- Add DSM tasks: `am_summary` (~06:55), `vix_snapshot` (~16:45).
- Re-deploy `oi_chain_eod` with the param-cap **batch fix**; `alembic upgrade head` to apply migration 0009 on the NAS DB.
