# Trend-collection catalog — bank vs pull-on-demand

**Principle: the trend is the signal.** One reading of gamma, skew, IV, flow, ownership or an
analyst target is noise; the *change over time* is the edge — Δ net gamma, the zero-gamma/flip level
migrating toward spot, skew percentile drifting, accumulation streaks, target-cut rate. So the data
splits into two buckets:

- **Bucket 1 — DATA TREND → bank forward** (snapshot daily/quarterly; value = the time series / percentile).
- **Bucket 2 — PULL ON DEMAND → don't bank** (current-state readout; pull live when a report opens).

**Source:** ~everything is **CVForge** — the Convex options backend (chains, greeks, tape, spot) plus its
157 FMP endpoints (fundamentals, institutional, analyst, price/vol history). Status: ✅ collected · 🟡 partial · ➕ to add.

---

# BUCKET 1 — DATA TREND (bank forward)

## A. Dealer gamma / positioning — *regime trend*

| Metric | Source (CVForge / table) | Cadence | Why we collect (trend signal) | Status |
|---|---|---|---|---|
| **Net GEX (total gamma) + Δ** | Convex `gxoi` → `greeks_snapshots`/`gex_rolling`; `get_gamma_history` | EOD | Δtotal-gamma = dealer hedging capacity. Rising → pinning/vol-suppression; falling/negative → fragility. The gamma trend flags regime shifts before price. | ✅ |
| **Zero-gamma / flip level + distance to spot** | `greeks/flip_point.py`; `get_gamma_history.gex_flip`,`flip_dist` | EOD | The pivot: above = damping, below = amplifying. Track **how the flip migrates vs spot** — flip rising toward spot = fragility; spot crossing flip = regime change. | ✅ |
| **Net DEX + sign flips** | Convex `dxoi` | EOD | Dealer directional inventory; a sign-flip = change in hedging direction. | ✅ |
| **Call/put-wall migration** | chain `gxoi`; `get_walls` | EOD | Walls = expected pin; **wall drift leads** support/resistance (level = state; migration = signal). | 🟡 bank |
| **VEX / CHEX** | synthesized `bs_vanna`/`bs_charm` | EOD | Vol-of-vol & charm-pinning trend into expiry. | 🟡 |

## B. Volatility — *HV / RV / IV / skew trend* (all in CVForge)

| Metric | Source | Cadence | Why we collect | Status |
|---|---|---|---|---|
| **ATM IV + history (per name)** | `skew_snapshots.atm_iv`, `vol_richness.iv_atm`/`iv_rank` | EOD | IV level & own-percentile trend = rich/cheap. | ✅ banked |
| **HV / RV (realized) history** | `quotes_daily.rv20/rv60` (closes) + `swing_features.rv20` (CVForge aggs); `prices/realized_vol.py` | EOD | Per-name annualized close-to-close HV, full daily history. | ✅ exists — residual: extra windows, calendar-day HV, index-ETF HV, IV-vs-trailing-HV |
| **IV term structure (per-name + index)** | `iv_term_snapshots` (per-name, const-maturity 30/60/90) + `iv_tenor` (index) + `skew_snapshots.atm_iv` (nearest-exp) + `vol_richness.term_slope` | EOD | Term-slope + backwardation trend, non-sawtoothing. | ✅ per-name const-maturity BUILT + index |
| **25Δ risk-reversal + 252d %ile** | chain; `get_skew_history` | EOD | Skew trend = directional fear/greed; %ile flags extremes. | 🟡 skew backfill |
| **Butterfly (25Δ wings) + %ile** | chain; `skew_snapshots` | EOD | Tail-pricing / wing-demand trend. | 🟡 |

## C. Flow / accumulation — *accumulation vs distribution trend*

| Metric | Source | Cadence | Why we collect | Status |
|---|---|---|---|---|
| **Net premium 4-way + accumulation score** | TAS tape → `tas_daily_flow`; `get_flow_report` | EOD | Accumulation vs distribution trend; net-buy streaks; building vs bailing. | ✅ market-wide |
| **Per-contract lifecycle (repeat builds)** | `tas_daily_contract` | EOD | Sustained strike accumulation = conviction. | ✅ |
| **OI changes (ΔOI by strike) + ΔIV** | chain; `get_oi_changes` | EOD | Open/close by strike; ΔOI+ΔIV → demand-led (buy) vs supply-led (write). | ✅ |

## D. Sentiment — *slow-trend layers (new)*

| Metric | Source (FMP via CVForge) | Cadence | Why we collect | Status |
|---|---|---|---|---|
| **Institutional ownership** (%, holders, net Δsh, put/call) | `institutional-ownership` | Quarterly | 13F accumulation vs distribution across quarters — catch the pivot. | ➕ |
| **Analyst targets + rating consensus** | `price-target`,`grades-consensus` | Weekly / on-change | Target-cut rate + consensus drift; price-vs-target gap = catch-down/up risk. | ➕ |
| **Upgrades / downgrades (events)** | `upgrades-downgrades` | On-change | Grade-change events lead re-rating. | ➕ |
| **EPS/revenue estimate revisions** | `analyst-estimates` | Weekly | Revision trend leads the multiple re-rate. | ➕ |

## E. Context / macro — *already market-wide*

| Metric | Source | Cadence | Why | Status |
|---|---|---|---|---|
| **VIX complex** (VIX/VVIX/term/VRP/OAS) | `get_vix` | EOD | Vol backdrop; market-wide vs idiosyncratic. | ✅ |
| **Index skew** (SDEX/SKEW/dispersion/COR) | `get_index_skew` | EOD | Tail-hedging & correlation-regime trend. | ✅ |
| **Fundamentals** (rev/RPO/margin/FCF/debt) | FMP | Quarterly | Underlying story; RPO & FCF trend. | 🟡 factor build |

---

# BUCKET 2 — PULL ON DEMAND (do not bank)

| Metric | Source | Why not banked |
|---|---|---|
| Wall **levels** | `get_walls` | State; only the *migration* (Bucket 1) is signal |
| ATM straddle / expected move | `get_straddle` | Recompute from live chain when needed |
| Technicals (RSI/MACD/BB/ATR) | FMP price → computed | Derivable any time from price |
| Current per-strike greeks snapshot | Convex chain | Live map; history is the aggregate (Bucket 1) |
| Price probability-cone | computed (spot + IV) | Pure computation, no data to store |
| Next-earnings date | FMP `earning-calendar` | Single lookup |

Cheap live CVForge pulls — banking them adds cost without signal.

---

# BUILD LIST — what we need to build (one go, in order)

1. **Sentiment collector** — **BUILT but PARKED (2026-07-17).** Institutional/analyst FMP endpoints are paywalled (CVForge proxy persistent 502, direct free key 402). Migration 0034 + job + tests are in place; the weekly schedule is commented out in `runner.py`. Re-enable with a paid FMP tier or a CVForge allowlist add (then repoint `fetch_inputs` to the direct `FmpClient`). Institutional/analyst stay **on-demand (web)**.
2. **HV/RV history** — **already exists** (`quotes_daily.rv20/rv60`, per-name daily, full history; + `swing_features.rv20`). Residual (optional): extra windows (rv10/rv120/rv252), exact calendar-day HV, index-ETF HV, and a banked IV-vs-*trailing*-HV richness (`vol_richness` today uses forward-RV).
3. **Per-name IV-term** — **BUILT 2026-07-17.** Constant-maturity per-name term (`scheduler/jobs/iv_term_snapshots.py`: reads stored `oi_chain_eod` + shared `cm_interp` at 30/60/90 → writes the shared `iv_tenor_snapshots` table, so `get_iv_tenor(symbols=[...])` surfaces it per name; no vendor call, **no new migration**, tests + `run_iv_term.bat`, scheduled 16:52 ET). Replaces the sawtoothing nearest-expiry `skew_snapshots.atm_iv` term. ▶ run the bat + add a 16:52 NAS DSM task.
4. **Complete skew backfill** — run `scripts/skew_backfill.py` + add the 16:55 NAS DSM task (importer already built).
5. **Tier-2 aggregate descriptors** — one row/name/day of net GEX/DEX / flip / ATM-IV / skew for the broad ~2,000 (sub-GB/yr), so wall-migration + gamma-trend exist universe-wide.

*Scoping lesson (2026-07-17): HV/RV + IV-term were ~80–90% already built — surface/use them before adding anything.*

## Collection tiers (scale to ~2,000 names)
- **All ~2,000** — market-wide tape flow (§C) + sentiment (§D) + aggregate descriptors (step 5). ~sub-GB/yr.
- **Focus list (~50–200)** — full per-strike greeks/skew/IV-term/walls + intraday, banked history (~65 GB/yr EOD for 2,000 makes per-strike a focus-list feature).
- **On-demand** — full live snapshot for any name (CVForge covers the universe).

*This catalog is the collection spec — add a row when a new trend signal is identified.*

_Last updated: 2026-07-17._
