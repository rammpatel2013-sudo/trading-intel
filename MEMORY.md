# MEMORY.md — Working memory for trading-intel

Living document. Update at the end of every working session. Tells future-you (and any AI assistant) where things stand and what's next.

---

## Current phase

**Phase 1 day 2 DONE (2026-05-21) — moving to Phase 1.5 / calibration.** First real ingestion pipeline live: watchlist Greeks snapshot pulls from Convex and writes to `greeks_snapshots`. Verified end-to-end (13/13 tickers written, pytest green, flip points populating).

**Status as of 2026-05-21:**
- `greeks_snapshot` job runs clean: 13 rows/run, GEX/DEX/VEX/CHEX + flip point + ATM IV per ticker
- Sanity check passed: SPX GEX ~$1.55B, single names $20–190M, SPY net ~$16M (calls/puts near-cancel intraday). Flip points populate for 12/13 (SMCI null — no zero-crossing in ±10%, plausible)
- See decision log (2026-05-21) for the ConvexValue response-shape gotchas + formula revision

**Status as of 2026-05-22 (Phase 2 — dashboard made visible + price history):**
- **Per-ticker dashboard page** (`dashboard/pages/1_Ticker.py`, Roadmap A1 DONE): price + SMA20 + Bollinger + GEX overlay, net GEX/DEX by strike (rolling avg + normal fit, marks flip & spot), RSI(14), call/put walls, day-over-day change panels. Pure data prep in `dashboard/ticker_data.py` (unit-tested).
- **Intraday 0DTE/1DTE volume flow** (SPX/SPY/QQQ): volume-weighted gamma/vanna/charm (cumulative + 5-min interval), `greeks/intraday_flow.py` (pure) + `scheduler/jobs/intraday_flow.py` (5-min RTH cron, market-hours guard) + `intraday_flow` table (migration **0005**) + auto-refreshing page `pages/2_Intraday_0DTE.py`. Config `INTRADAY_SYMBOLS`/`INTRADAY_STRIKE_RANGE` (±3%)/`INTRADAY_MAX_DTE`.
- **Daily price history**: `clients/prices.py` `YFinancePriceSource` behind new `PriceDataSource` Protocol (SPX→^GSPC); `prices/realized_vol.py` (rv20/rv60); `scheduler/jobs/quotes_daily.py` + `scripts/backfill_quotes.py` (one-time, 5y) + 16:45 ET daily cron. quotes_daily backfilled for full watchlist.
- **Bugs fixed (now tested):** quotes_daily.symbol FK→tickers (job now seeds `tickers`; SQLite tests enforce FKs); `quotes_daily.volume` widened int4→BigInteger (migration **0006**) — ^GSPC index volume overflowed int4.
- **Migrations now at 0006.** ~124 tests, pytest green, ruff clean.
- **Watchlist overview** (`pages/3_Watchlist.py` + `scripts/watchlist_report.py`, shared `dashboard/watchlist_metrics.py`): per-ticker net GEX + dir + weekly Δ, C/P OI, vol/OI, skew, walls + CW distance, gamma regime (spot vs flip), gamma concentration ±3%. Descriptive gamma-squeeze read-through (NOT a prediction — rule 4 / C5 gate).
- **Options flow** (`flow_snapshots` table, migration **0007**; `scheduler/jobs/flow_snapshot.py` 30-min RTH; `dashboard/flow_data.py` + `pages/4_Flow.py`): call/put notional, P/C tilt, net premium, largest prints + multi-leg packages via `strategies/options_flow.py`. Added `flow_chain`/`time_and_sales` to the OptionsDataSource Protocol.
- **Fixed-strike vol charts + Fibonacci** (Ticker page): fib overlay (`prices/fibonacci.py`), fixed-strike ΔIV-by-strike chart + call/put wall-drift chart (`dashboard/changes.load_fixed_strike_changes`, `dashboard/walls.wall_history_frame`). Work off existing data — no migration.
- **Research-driven dynamic watchlist**: `watchlist_entries` table (migration **0008**); LLM extractor `synthesis/watchlist_extract.py` (+ `WATCHLIST_EXTRACTION_PROMPT`); ingest `memory/watchlist_ingest.py` (`python -m trading_intel.memory.watchlist_ingest <file>`, needs Ollama); `dashboard/dynamic_watchlist.py` + `pages/5_Research_Watchlist.py` (surfaced tickers + rationale/sentiment, cross-referenced with regime metrics).
- **Effective watchlist** (`trading_intel/watchlist.py` `effective_symbols`): static `.env` WATCHLIST ∪ active `watchlist_entries` symbols. Wired into greeks/chain/gex_rolling/flow/quotes collectors + watchlist & flow dashboard pages (intraday 0DTE stays the focused SPX/SPY/QQQ set). Graceful fallback to static when the table/DB isn't available.
- **Company-research drop folder** `research/company/` (gitignored): drop PDFs/docx → `python scripts/sync_research_watchlist.py` runs Ollama ingest (`memory/watchlist_ingest.ingest_folder`) → adds tickers to `watchlist_entries` → backfills price history for the new tickers (`quotes_daily.run(symbols=...)`). Next collector cycle, those tickers get full regime collection + appear on the dashboard.
- **intraday_flow 48h retention**: `scheduler/jobs/prune_intraday.py` (hourly cron) deletes per-strike rows older than `INTRADAY_RETENTION_HOURS` (48).
- **Migrations now at 0008.** pytest green, ruff clean.
- **ACTIVATED on NAS 2026-05-23.** Code pushed (commit `0b84d66`), NAS image rebuilt with the new jobs, and three DSM Task Scheduler tasks added: `trading-intel intraday` (5-min, Mon–Fri 09:30–16:00), `trading-intel flow` (30-min, Mon–Fri 09:30–16:00), `trading-intel daily prices` (16:45 daily → `quotes_daily && prune_intraday`). Smoke-tested (`prune_intraday` → EXIT 0). First live 5-min volume = **Tue 2026-05-26** (Mon 25th = Memorial Day). See `### NAS deployment` below for how the collector actually runs + the gotchas hit. Week-over-week metrics still data-gated (need ≥1 week of history, live from 2026-05-22).

**Status as of 2026-05-23 (Phase 2.1 — Daily AM report DONE):**
- **Daily AM report** (`synthesis/am_summary.py` + `scheduler/jobs/am_summary.py` + `dashboard/pages/0_AM_Report.py` + reader `dashboard/am_report_data.py` + `AM_SUMMARY_PROMPT`, commit `7d7da5b`): research-watchlist-aware morning regime note. Pure `build_am_context` composes the effective watchlist, per-ticker regime metrics, flow highlights, SPX/SPY/QQQ 0DTE read, week-over-week ΔGEX, and research-surfaced tickers (rationale/sentiment, flagged research-vs-static) — all from STORED data (no live Convex pull; it summarizes what the greeks/chain/flow/intraday/quotes collectors wrote). `render_am_markdown` calls the local Ollama LLM (rule 7) with a deterministic tables-only fallback if Ollama is down. Idempotent upsert into `am_summaries` (`ON CONFLICT (date) DO UPDATE` — re-running anytime refreshes today's row). Page sorts to top (`0_`). Registered 07:00 ET in `runner.py`. Discord delivery gated behind `AM_REPORT_SEND_DISCORD` (no-op — no `clients/discord.py` yet). Descriptive only (rule 4). 172 tests green, ruff clean on changed files.
- Verified locally 2026-05-23: job wrote today's row, `used_llm=true`, 13 symbols, `research=0` (no active research tickers, so the research section shows the empty-state line). Renders on the AM Report page.
- **NAS deploy pending (Mithil, PowerShell):** push `main`, then add a DSM task `... trading-intel python -m trading_intel.scheduler.jobs.am_summary` Daily ~06:55 (runner cron ignored on NAS). No migration — `am_summaries` already at head `0008`.
- **Sandbox gotcha (NEW, important):** the cowork mount intermittently served STALE/TRUNCATED views of just-edited files during verification (the canonical Windows files were fine via the Read tool). Lint/test against reconstructed clean copies and verify canonical via Read. Also: pytest reused a stale assertion-rewritten `.pyc` the mount could not delete (EPERM) — run `pytest --assert=plain -p no:cacheprovider` (plus the `datetime.UTC` shim) to bypass it. `.git/index.lock` could not be removed (EPERM) — `mv` it aside; the `tmp_obj_*` unlink warnings during `git add`/commit are benign (confirm with `git fsck --connectivity-only`).

**Status as of 2026-05-23 (Track 1 — GEX-by-strike time series DONE):**
- **GEX surface (strike × time)** for SPX/SPY/QQQ: pure helper `dashboard/gex_surface.py` (`load_gex_strike_series` → tidy long `[ts, strike, net_gex]` by stacking `ticker_data.gex_by_strike` per stored `greeks_chain` ts; `gex_strike_matrix` pivot; `spot_flip_overlay` over `load_snapshot_history`; optional `expiry_within_days` near-term filter) + thin page `dashboard/pages/6_GEX_Surface.py` (Plotly diverging zero-centered RdBu heatmap, spot/flip overlay lines, latest-snapshot bar companion, symbol/days/expiry controls, empty-state). Per Mithil's pick, used a **dedicated multi-ts gxoi reader inside `gex_surface.py`** (reusing `ticker_data._chain_rows_to_frame`) rather than extending `changes._rows_to_chain` — `changes.py` untouched. **Strike-range filter:** `load_gex_strike_series(pct_range=0.03)` trims each day's chain to ±3% of that day's spot (spot from `greeks_snapshots`, matched by day; days with no stored spot keep all strikes) so the heatmap focuses near-the-money instead of the full ±15% chain pull; page has a "Strike range (± % of spot)" slider (1–15%, default 3) + a "Show full chain" toggle (`pct_range=None`). Descriptive only (rule 4). 5 new tests in `tests/dashboard/test_gex_surface.py` (SQLite, per-table create, `datetime.UTC` shim, `--assert=plain -p no:cacheprovider`) green; ruff clean on changed files. **Daily resolution** (one column/trading day from the daily `chain_snapshot`); fills in as sessions accrue (live from week of 2026-05-26). Intraday cadence deferred. **NAS:** no migration, no new collector — pure read-side; nothing to deploy beyond pushing `main`.
- Sandbox note (recurring): the cowork mount served **truncated** copies of just-edited files to bash/ruff/pytest mid-session (a test file cut at line 106, a NUL/binary artifact). The canonical Windows files were fine via Read. Verified by linting/testing reconstructed clean copies in `/tmp` — keep doing this.

**Status as of 2026-05-23 (OI & flow change — day-over-day positioning analytics DONE, code-complete):**
- **Goal:** EOD pull of the full ~180-day chain per watchlist symbol, stored per-strike, then compare today vs yesterday — ΔOI (our own diff + Convex native `oi_ch`), today's volume, conversion (|ΔOI|/volume = new positioning vs churn), per-strike net-signed GEX contribution and its ΔGEX, rolled up to total ΔGEX + call-vs-put ΔOI. Descriptive decision-support, not signals (rule 4).
- **Convex field:** the OI-change code is **`oi_ch`** (confirmed in the convexlib get_chain README; my earlier probe guesses oi_chg/oichg/etc. were all invalid). Added `oi_ch` to `_CHAIN_PARAMS` + normalized to `oi_change` in `clients/convex.py`; `chain()` now wraps `_fetch_chain()` with a **graceful fallback** — if Convex 400s on `oi_ch` it retries once without it and fills `oi_change=NaN` (one bad param can't brick the pull). Added `oi_change` to the `OptionsDataSource.chain` Protocol contract (rule 1). Updated `tests/clients/test_convex.py` (`_DATA_PARAMS`/`_row` + assert oi_change present). **Mithil: confirm with `c.probe_param('SPY','oi_ch')` before NAS deploy.**
- **New table** `oi_chain_eod` (migration **0009**, model in `models.py`): per (symbol, ts[day], expiry, strike, cp) with oi, oi_change, volume, dte, gxoi/dxoi/vxoi, delta/gamma/iv; unique natural key. Config: `OI_CHAIN_WINDOW_DAYS=180`, `OI_CHAIN_RETENTION_DAYS=90`.
- **Collector** `scheduler/jobs/oi_chain_eod.py` (reuses `source.chain_long()` — the same wide ±20%/~40-exp pull `gex_rolling` already does, filtered to ≤180 DTE; idempotent, ts floored to day) + retention `scheduler/jobs/prune_oi_chain.py`. Registered in `runner.py` at 16:35 (after gex_rolling) and 02:20.
- **Bug fixed on first NAS run (2026-05-23):** the single multi-row `pg_insert(...).values(records)` blew Postgres's 65535 bound-param cap (`psycopg.OperationalError: number of parameters must be between 0 and 65535`) — a 180d wide chain is far more than ~4095 rows (65535/16 cols). Now **batched** at `_INSERT_BATCH=1000` rows/statement. Regression test `test_run_batches_inserts` (monkeypatches `_INSERT_BATCH`+`effective_symbols`, counts INSERTs). `chain_snapshot` never hit this — its near-term pull is small. **First NAS EOD run confirmed: table exists (migration 0009 applied), image rebuilt, job started 13 symbols — only the param cap failed; needs re-deploy with the batch fix.**
- **Analytics** `dashboard/oi_changes.py` (pure): `load_recent_eod`, `build_oi_change_frame` (outer-join on expiry/strike/cp; new strikes treated as 0→today), `summarize_oi_change` (descriptive note), `top_oi_changes`. **Page** `dashboard/pages/7_OI_Flow_Change.py`: ΔGEX metrics, diverging per-strike ΔGEX bar, biggest-changes table, read-through caption; empty-state until ≥2 EOD snapshots.
- **Verified:** 8 new tests pass (collector window/field mapping + analytics diff on SQLite) + 2 isolated tests for the Convex happy/fallback paths; ruff clean on the changed files that the mount would serve (oi_changes, both test files, collector, prune, migration). `convex.py`/`runner.py`/`models.py`/`config.py` couldn't be lint-run via the mount (severe stale/truncated reads this session — see gotcha) but carry only small pattern-mirroring changes.
- **NAS deploy (Mithil):** `alembic upgrade head` from the laptop (applies 0009 to the NAS DB) + add DSM tasks for `oi_chain_eod` (~16:35) and `prune_oi_chain` (daily), same docker wrapper. **Also still pending: chain_snapshot/greeks_snapshot have NO NAS DSM task** (only intraday/flow/daily-prices run there) — that's why the GEX surface and this OI study won't accumulate until those EOD collectors are scheduled too.

**Status as of 2026-05-23 (Session — methodology RAG substrate + ΔIV positioning + fixed-strike heatmap + VIX, code-complete):**
- **Methodology RAG substrate DONE (item 2).** The `chunks` pgvector table + `embedding Vector(768)` + IVFFlat index already existed (migration 0001) so **no migration**. New: `memory/chunking.py` (deterministic full-text chunker); `memory/embeddings.py` (chunk→embed→`INSERT ... CAST(:v AS vector)`, kept OUT of the ORM so `models.py` stays Postgres-neutral + SQLite tests work; `delete_chunks`/`count_chunks`); `pdf_pipeline.ingest_document` now embeds on ingest (best-effort, `--no-embed`); `memory/retrieval.py` (cosine `embedding <=> CAST(:q AS vector)` filtered by `kind`, + `format_kb`); `surface_report.load_kb_context` upgraded to semantic retrieval (query from surface metrics) with file-concat fallback. 35 memory/surface tests green, ruff-clean.
- **Auto-scan + re-index DONE.** `memory/sync_knowledge.py` reconciles a drop folder vs `documents` by **path**: new→ingest, unchanged→skip, **edited→supersede** (delete old chunks/theme_obs/watchlist_entries + playbook, re-ingest), removed→**prune (opt-in `--prune-removed`)**, + **backfills embeddings** for docs with zero chunks. Both `research/doc/` (methodology) + `research/company/` (research). CLI: `python -m trading_intel.memory.sync_knowledge`.
- **22 methodology PDFs** in `research/doc/`. Mithil's first ingest (old code, no embeddings) → **then `python -m trading_intel.memory.sync_knowledge --skip-research` backfills embeddings** (no LLM regen). That run stalled ~7-9 docs (large PDF on pdfplumber fallback or Ollama thrash); ingest is per-doc-committed + sha-idempotent so Ctrl-C + re-run resumes.
- **ΔIV positioning analytic DONE (item 1).** `oi_changes.py`: per-strike **ΔIV** + descriptive `positioning` label (`classify_positioning` = ΔOI sign × ΔIV sign: opening demand-led / opening supply-led / closing-unwind / closing-into-firmer-IV) + `mean_d_iv`. Mithil's "new-buy vs close, confirmed by IV" idea. On `pages/7_OI_Flow_Change.py`. Live after Tue 5/26. 8 tests green.
- **Fixed-strike viz redesigned DONE (Track 2).** `changes.fixed_strike_change_matrix` (strike×expiry pivot); Ticker `_fixed_strike_panel` now a diverging ΔIV **heatmap**. 6 tests green.
- **VIX dashboard DONE (Track 3) — CBOE endpoints VERIFIED LIVE 2026-05-26.** `clients/fred.py` (VIX `VIXCLS`, HY `BAMLH0A0HYM2`, IG `BAMLC0A0CM`; **MOVE not on FRED → None**), `clients/cboe.py` (VVIX `_VVIX` + term `_VIX9D/_VIX/_VIX3M/_VIX6M` from cdn.cboe.com — **URL + JSON keys CONFIRMED**: live shape is `{timestamp, data:{current_price,...}, symbol}`; `_parse_price` unwraps `data` then reads `current_price` first → correct AS WRITTEN, no code change needed. Live readings (delayed feed, last trade 5/22 since Mon 5/25 = Memorial Day): VIX9D 14.07 / VIX 16.75 / VIX3M 20.03 / VIX6M 22.35 (clean contango) / VVIX 91.16. Caveat: the less-liquid tenors (`_VIX9D/_VIX3M/_VIX6M`) return open/high/low=0.0 with current_price==close — level still correct; only `_VIX`/`_VVIX` carry real OHLC. Still TODO: run `vix_snapshot` against the DB once to confirm a row writes (vega_zone, vvix_sd20). graceful None on any failure), `scheduler/jobs/vix_snapshot.py` (idempotent get-or-create upsert; vix_sd20 from FRED, vvix_sd20 from history; vega_zone), `dashboard/vix_view.py` (pure), `pages/8_VIX.py`. Registered `runner.py` 16:45 ET. **No migration** (`vix_data` exists). 17 tests green.
- **Sandbox gotcha (WORSE):** the mount served STALE/TRUNCATED/NUL-corrupted copies of many files (incl. large canonical files via bash); the Read/Edit/Write tools were always authoritative. Verified by reconstructing clean `/tmp` copies via **heredoc** (not `cp`, which propagated truncation) + pytest/ruff there. Test deps installed fresh in-sandbox.
- **Hand-off (Mithil):** (1) finish 22-PDF ingest → `sync_knowledge --skip-research` to embed; (2) push `main`; (3) NAS DSM tasks: `am_summary` ~06:55, `vix_snapshot` ~16:45, + the `oi_chain_eod` re-deploy w/ batch fix; (4) `scripts/verify_oi_flow.py` after Tue 5/26 EOD; (5) ~~verify CBOE endpoints~~ ✅ DONE 2026-05-26 (verified live, parser correct, no change); (6) optional laptop nightly `sync_knowledge` (needs Ollama).

**Status as of 2026-05-26 (Session — vol-richness scanner, first 2 build-order items):**
- **CBOE endpoints verified live** (see Track 3 entry above) — parser correct, no code change; updated `clients/cboe.py` docstring + 3 MEMORY spots.
- **`prices/forecast_vol.py` DONE** — HAR-RV + EWMA forward-RV forecaster (the missing forward half of `vrp_pts`). 12 tests.
- **`vol/richness.py` DONE** (new `vol/` package) — VRP + percentile/IV-rank ranking, constant-maturity ATM-IV interpolation, cold-start handling. 12 tests.
- **ROADMAP updated** — agreed build/verify/deploy punch list appended as the "Session 2026-05-26" section in `docs/ROADMAP.md`.
- **`vol/term_skew.py` DONE** — term slope + 25Δ skew vs history + the mandatory VEGA/VIX regime gate (`gated_label` overlay). 9 tests. Decoupled from the DB layer (local zone constants, no sqlalchemy import).
- **Migration `0013_vol_richness` DONE** (head was 0012, not 0011 as the 5/24 scoping assumed). **Mithil: `alembic upgrade head` to apply on the real DB.**
- **`scheduler/jobs/vol_richness.py` DONE + registered** in `runner.py` at 16:40. 4 tests. **BUGFIX 2026-05-26 (caught on Mithil's real machine):** `surface_metrics` lives in `synthesis/surface_report.py`, NOT `greeks/surface.py` — the original import `from trading_intel.greeks.surface import ... surface_metrics` raised ImportError. The sandbox clean-tree test had masked it (I'd colocated `surface_metrics` into the reconstructed surface.py). Fixed by dropping the import and computing the 25Δ skew directly off the `DeltaSurface` in `_skew_at_horizon` (keeps the job decoupled from the synthesis/report layer). Re-verified: 4 tests pass against a surface.py with NO `surface_metrics`. **Lesson: the heredoc clean-tree mirrors must match the REAL module layout, not a convenient colocation.**
- **Compute + persistence layer of the scanner is COMPLETE:** forecast_vol → richness → term_skew → 0013 migration → EOD job → runner registration. 49 tests across the new code.
- **Dashboard page DONE + surface.py warning silenced.** First live job run wrote **122 rows / 61 symbols** on Mithil's machine (2026-05-26).
- **Vol-Richness page display formatting (2026-05-26):** raw decimals were hard to read, so added pure `scale_for_display()` (decimal vol cols ×100 → vol points; 0..1 scores → 0..100) + readable `st.column_config.NumberColumn` headers/formats on the page (ATM IV %, Fcst RV %, VRP (vol pts), Richness, IV rank, Term slope (pts), 25d skew (pts), Regime). Dropped the duplicate `vrp_pctile` column (== `richness_score`). 9 data-layer tests. **Ruff gotcha logged:** in `dashboard/pages/**` (linted with RUF, unlike tests), ambiguous-unicode chars `−` (U+2212), `·` (U+00B7), `×` (U+00D7), `→` (U+2192) trip RUF001/002/003 — use ASCII (`-`, `--`, `x`, `->`). `↔`, `—`, `Δ`, emoji are fine (precedent: term_skew/vix_view).
- **Vol-richness scanner: scoped Phase-A core is now COMPLETE** — forecast_vol, richness, term_skew, migration 0013, EOD job, runner registration, dashboard page. 56 tests across the new code.
- **Remaining vol-richness items (optional / later):** vol cone / expected-move envelope, AM-report top-3 wiring, `scripts/backtest_vol_richness.py` (the validation gate before any `strategies/` promotion), and the `vomma` Convex field add. Optional viz polish (treemap / DAOI / expected-range band) still deferred.
- **Sandbox gotcha RECURRED HARD this session:** the mount served TRUNCATED (cut mid-line at `.reset_ind`) AND NUL-corrupted copies to `cat`/`cp`/`ruff`/`pytest` against canonical paths, nondeterministically (pytest passed early, then a later identical run hit `ValueError: source code string cannot contain null bytes`). Read/Edit/Write tools stayed authoritative. **Resolution that worked:** reconstruct a clean tree in `/tmp` via **heredoc** (cp/python-read-from-mount propagate the corruption) + run ruff/pytest there. Final authoritative result: 24/24 tests pass, ruff clean on both modules + tests.

**Status as of 2026-05-26 (Session cont. — dashboard improvements, batch 1 of 4):**
- Mithil requested a big dashboard punch list (4 clusters: freshness+color cross-cutting / quick-wins+ordering+text / ticker-page overhaul / intraday-0DTE overhaul) — full list in chat. Doing it in passes.
- **AM report "stuck on 5/23" was NOT a bug** — the `am_summary` job hasn't run since Fri 5/23 (Sat/Sun/Memorial-Day, + never added as a NAS DSM task). Fix: run `python -m trading_intel.scheduler.jobs.am_summary` and/or add the NAS task. Added a **stale-warning banner** to `pages/0_AM_Report.py` (warns when newest report date < today, with the refresh command).
- **Foundations built (pure, tested):** `dashboard/freshness.py` (`format_et` — naive stored ts treated as ET since the NAS runs ET; `freshness_caption`, `age`, `staleness`) + `dashboard/styling.py` (`gex_dir_color`/`gamma_regime_color`/`zone_color`/`richness_color`/`staleness_color` + `flip_distance_pct`/`flip_state`/`flip_proximity_color` — "how far spot is from the GEX flip"). 12 tests green, ruff clean.
- **Wired so far:** AM report (stale warning) + Vol-Richness page (Richness/regime colour via pandas Styler `.map`).
- **Batch 2 done (2026-05-26):**
  - **`.N`/`.TO` suffix stripping:** `watchlist_extract.normalize_symbol` strips Refinitiv/Yahoo exchange suffixes (RY.TO->RY, AAPL.N->AAPL) preserving share classes (BRK.B); applied in `parse_candidates`. NOTE: only fixes FUTURE ingests — existing `watchlist_entries` rows with suffixes need a re-ingest/cleanup. + 2 tests.
  - **OI-flow ascending-by-strike:** `oi_changes.top_oi_changes(..., sort_by_strike=True)` selects top-N by magnitude then re-orders by strike ascending; `pages/7_OI_Flow_Change.py` now passes it (chart + table read 6400P->9000C) + an ET "Snapshot pulled" freshness caption. + 1 test.
  - **Page-number collision fixed:** `9_Vol_Richness.py` clashed with `9_Market_Timing.py` -> renamed to **`12_Vol_Richness.py`** (via `mv`).
- **MOUNT REALITY this session:** the cowork mount truncates/NUL-corrupts essentially every canonical bash/ruff/pytest read now, so self-lint/self-test in-sandbox is unreliable. Mitigation: verify pure logic via heredoc standalone reconstruction; rely on Mithil's real-disk `ruff`/`pytest` (authoritative) for final confirmation. Edits via Read/Edit/Write are authoritative.
- **Batch 3 done (2026-05-26):**
  - **Watchlist** (`watchlist_metrics.py` + `pages/3_Watchlist.py`): new `flip_distance(spot, gex_flip)` -> "Flip dist" column (how far spot is from the flip / about to convert) in DISPLAY_LABELS + _PCT_COLS; page colours **Gamma regime** + **GEX dir** via pandas Styler `.map`, + ET "Greeks snapshot pulled" caption. + `test_flip_distance`.
  - **Market timing** (`pages/9_Market_Timing.py`): ET "VIX data" freshness caption; gamma regime + VIX zone rendered with Streamlit `:green/:orange/:red[...]` coloured markdown (`_regime_md`/`_zone_md`).
  - **GEX surface** (`pages/6_GEX_Surface.py`): ET "Latest snapshot" caption. (The existing sidebar "Strike range (± %)" slider ALREADY controls the bottom latest-profile bar — no change needed.)
  - **VIX** (`pages/8_VIX.py`): added a "What the decomposition means" expander explaining sticky-strike (mechanical) vs parallel-shift/put-gradient/convexity (true fear).
- **Ticker page overhaul DONE (2026-05-26):** `ticker_data.py` + new `available_chain_dates`/`load_chain_at` (chain-by-date) + pure `near_spot(by_strike, spot, pct)` strike-range filter (+ test). `pages/1_Ticker.py`: sidebar **Chain date** selector + **Strike range (± % of spot)** slider (+ full-chain toggle) trimming GEX/DEX + the fixed-strike ΔIV heatmap; **RSI moved directly below the price chart**; **call/put wall markers** drawn on the GEX bar; **ATM-IV-over-time** line added beside the fixed-strike heatmap (the day-over-day ATM vol-change graph); the wall/change markdown **tables moved into an expander** (viz is primary); ET "Chain snapshot" freshness caption. NOTE: spot/flip still come from the LATEST snapshot even when an older chain date is picked (minor; refine later if needed).
- **Intraday-0DTE overhaul DONE (2026-05-26) — LAST dashboard-list item:** `ticker_data.volume_by_strike_side(frame, col)` (pure call/put volume split, + test). `pages/2_Intraday_0DTE.py`: ET "Last 5-min update" freshness caption (replaced the bare %H:%M); net gamma/vanna/charm metrics now formatted **K/M/B** (`_fmt_mb`) and **colour-coded** green/red via `:color[]` markdown (`_greek_md`); traded-volume-by-strike replaced with a **calls-vs-puts diverging bar** (`_volume_split_bar`, calls + / puts -); added a descriptive **hedge-shift read** (`_hedge_read`) — rule-4-safe text on how the current 0DTE gamma/vanna/charm signs shape dealer hedging into the close (NOT a forecast). Also fixed a pre-existing `×` (RUF001) in `pages/1_Ticker.py`.
- **ENTIRE dashboard punch list from Mithil is now COMPLETE** (all 4 clusters). Pending Mithil verification of the intraday batch (ruff + pytest).

**Status as of 2026-05-26 (Session cont. — Delta-notional flow chart, NEW feature):**
- Mithil shared a reference "delta notional flow" chart (price overlaid with cumulative call/put delta notional, All-Trades vs Next-Expiry, ~5-min refresh) and chose "build the all-expiry collector first". Built the full stack:
  - **`greeks/delta_flow.py`** (pure): `delta_notional_split(chain, spot)` = `Σ(delta·volume·spot·100)` by side, for ALL expiries and the NEXT (nearest) expiry; calls +, puts - (delta sign carried). 3 tests. **`volume` is cumulative day-volume so each snapshot IS the running cumulative line.**
  - **`DeltaFlow` model + migration `0014_delta_flow`** (down_revision 0013): table `delta_flow` (symbol, ts, source, spot, next_expiry, call/put_notional_all, call/put_notional_next; UQ symbol+ts+source). Round-trip verified on SQLite; model<->migration cols match.
  - **`scheduler/jobs/delta_flow.py`**: 5-min RTH collector, pulls `source.chain_long()` (ALL expiries) per `intraday_symbols` (SPX/SPY/QQQ), `build_record` -> idempotent ON CONFLICT(symbol,ts,source) DO UPDATE. Registered in `runner.py` (`*/5` 09-16 mon-fri). 3 tests (build_record + upsert compile).
  - **`dashboard/delta_flow_data.py`** (`load_delta_flow_day` latest-session series + `available_symbols`) — 4 tests. **`pages/13_Delta_Flow.py`**: price (white, left axis) + 4 delta-notional lines (orange calls-all / blue puts-all / green calls-next / light-blue puts-next, right axis `$~s`), symbol selector, 5-min auto-refresh, ET freshness. (Retail line from the reference SKIPPED — no retail-tagged flow source.)
  - All lint-clean + logic-verified standalone (mount still corrupting; relied on heredoc/standalone + Mithil's ruff/pytest).
  - **Mithil TODO:** `alembic upgrade head` (apply 0014); add a NAS DSM task for `delta_flow` (5-min RTH); data accrues only during the live session. To add more symbols (Mag7/NVDA/...) later, extend `INTRADAY_SYMBOLS` (heavier — full-chain pull per symbol every 5 min).
- **Ruff gotchas confirmed this session:** in pages, `from __future__ import annotations` means annotations should be UNQUOTED (UP037) — write `-> pd.io.formats.style.Styler`, not the quoted form. `·` (U+00B7) is NOT flagged; `−`/`×`/`→` ARE. `timezone.utc` triggers UP017 (wants `datetime.UTC`, 3.11) — use an explicit `timezone(timedelta(0))` in tests to stay 3.10-safe. /tmp lint trees need `[tool.ruff.lint.isort] known-first-party=["trading_intel"]` or isort mis-splits imports.

**Status as of 2026-05-26 (Session cont. — timezone standardization + ticker live-spot):**
- Mithil reported charts starting at **14:30** (UTC). Root cause: collectors stamped naive `datetime.now()` = host-machine local tz (UTC on his box), and `chain_snapshot` even used `datetime.now(UTC)`. So intraday charts showed UTC and the `is_market_hours` guard was wrong on a non-ET host.
- **FIX (chosen: store wall-clock ET regardless of host):** new `trading_intel/timeutils.py` `eastern_now()` (naive ET via ZoneInfo). Swapped `datetime.now()`/`datetime.now(UTC)`/`date.today()` -> `eastern_now()`/`eastern_now().date()` in: `greeks_snapshot, chain_snapshot, gex_rolling, intraday_flow, flow_snapshot, oi_chain_eod, delta_flow, vol_richness, vix_snapshot` jobs + `synthesis/am_summary.build_am_context`. Dashboard already treats stored naive ts as ET (`freshness.format_et` + charts plot naive directly), so **NO display changes needed** — far less risk than UTC-aware + per-chart conversion. (Dropped now-unused `datetime` import in greeks_snapshot/gex_rolling/am_summary; dropped `UTC` in chain_snapshot.) timeutils: 2 tests, ruff clean. **EXISTING UTC-stamped rows still show 14:30 until re-collected; new data is ET.**
- **#26 DONE:** Ticker page — `ticker_data.snapshot_spot_flip(snaps, ts)` (spot/flip from the nearest snapshot, latest if None; + test); page now derives spot/flip from the SELECTED snapshot, and on the latest view overlays a live `_live_spot()` quote (yfinance, `_YF_MAP` SPX->^GSPC) so the spot marker tracks the tape (GEX/DEX profile stays from the stored snapshot); relabeled selector "Stored snapshot" with help. (gex_total/atm_iv top metrics still from latest snap — minor.)
- **GEX-live question (answered, NOT built):** GEX is NOT recomputed live. Per-strike GEX is gamma×OI from the STORED chain — recomputing with a new spot doesn't change it; only the flip-vs-spot + the marker move. For genuinely live GEX you need a fresh intraday CHAIN pull. Mithil's two-tier idea is right: add an **intraday GEX/chain refresh** (every few min, short retention / pruned EOD) for live view + keep the EOD snapshot for history. CANDIDATE next workstream.
**Status as of 2026-05-26 (Session cont. — intraday LIVE-GEX refresh, NEW two-tier feature):**
- Mithil wants live GEX (recompute as spot moves). Confirmed: GEX-by-strike is gamma×OI from the stored chain — needs a fresh intraday CHAIN pull, not a recompute. Two-tier: live (intraday, pruned EOD) + EOD snapshot (historical).
- **Scope chosen:** per-strike, **filtered to the near-money delta band |delta| 0.30-0.70** ("below 30 / above 70 no need"), **full effective watchlist**, ~10-min RTH, pruned EOD. (Heavy Convex load at 61 names — will make cadence/symbols config-knobbed + tight strike-range pull; Mithil can dial down on rate limits. Convex `source.spot()` gives live spot natively.)
- **Foundation DONE:** `greeks.intraday_flow.filter_delta_band(chain, lo=0.30, hi=0.70)` (+2 tests, logic verified). `LiveGex` model + migration **`0015_live_gex`** (down 0014): per (symbol, ts, strike, cp) spot/delta/gamma/iv/gxoi/dxoi, UQ symbol+ts+strike+cp, pruned EOD. Round-trip verified on SQLite; model<->migration cols match (12).
- **`.N`/`.TO` existing-rows cleanup:** `scripts/normalize_watchlist_symbols.py` (one-time) rewrites stored `watchlist_entries` symbols via `normalize_symbol`, dropping dups that collide with an already-normalized entry for the same doc. Idempotent. Logic verified on SQLite (RY.TO->RY updated, AAPL.N dropped as dup of AAPL, BRK.B preserved). **Mithil runs once:** `python scripts/normalize_watchlist_symbols.py`.
- **#28 + #29 DONE (live-GEX feature complete):** `scheduler/jobs/live_gex.py` (10-min RTH, `source.chain(strike_range=LIVE_GEX_STRIKE_RANGE)` -> `filter_delta_band` -> per-strike upsert; `build_records` + `_symbols` config-knobbed via `LIVE_GEX_SYMBOLS`, default effective watchlist) + `prune_live_gex.py` (24h retention) + registered in runner (`*/10` RTH + prune 02:30). Also fixed `prune_intraday`/`prune_oi_chain` cutoffs to `eastern_now()` (were `datetime.now()` — wrong window now ts is ET). Config knobs added (LIVE_GEX_*). Suppressed a pre-existing S105 false-positive on `SCHWAB_TOKEN_PATH`. **#29:** `dashboard/live_gex_data.load_live_chain` (latest FRESH live_gex within `max_age_min`, chain-shaped) + `live_spot`; **ticker page** now prefers fresh live_gex for the GEX/DEX-by-strike bars on the latest view (labeled "LIVE @ ts"), falls back to the stored snapshot. 4 data-layer tests + 3 collector/prune tests, logic verified.
- **`docs/NAS_TASKS.md` written** — full DSM task list + one-time migration/cleanup steps for everything this session.
- **Deferred (optional):** a live-GEX column on the GEX-surface page (the ticker page covers Mithil's actual spot/GEX concern). N/A
- **(historical note) earlier TODO was:** #28 `scheduler/jobs/live_gex.py` (10-min RTH collector + `prune_live_gex` + runner registration + config knobs) ; #29 wire ticker page + GEX surface to prefer fresh `live_gex` (label LIVE vs snapshot). Then `alembic upgrade head` (0015) + NAS DSM task.

- **NXE flow-card chart (the photo):** daily **bullish vs bearish premium** bars + cumulative trend (bullish = calls-bought + puts-sold; bearish = puts-bought + calls-sold) + 10-day bought/sold split. DATA CHECK: `strategies/options_flow.py` consumes `aggressor_side` (buy/sell) so the classification IS possible, BUT the stored `flow_snapshots` table only persists aggregates (call/put notional, net_premium, tilt) — NOT the bought/sold split. So building it needs **extending flow_snapshot + table to persist bullish/bearish (bought/sold-by-side) premium**, then a daily-aggregation trend page. CANDIDATE next workstream.

**Open items (next):**
- ✅ Fixed-strike vol viz redesigned (strike×expiry ΔIV heatmap). [Track 2 done]
- ✅ VIX dashboard built — **CBOE endpoints verified live 2026-05-26 (parser correct, no code change).** [Track 3 done]
- **AM-report RAG wiring** was deferred (item 2 scoped to substrate + surface-KB only) — wire retrieved methodology into `AM_SUMMARY_PROMPT` next.
- **NEXT major workstream: Vol-richness scanner** (mispriced-options-via-vol). Scoped 2026-05-24, NOT built — full checklist in the "Vol-richness scanner (PLANNED)" section just below.

## Vol-richness scanner (PLANNED — NOT built; scoped 2026-05-24)

**Goal:** identify improperly priced options through a volatility lens (IV rich/cheap vs *forward* realized vol) and surface a daily rich/cheap sheet to trade off. Descriptor-first (rule 4): Phase A writes NO signals; promote to `strategies/` only after the backtest proves edge — this is the on-ramp to the Phase-5 probability model.

**Horizons (LOCKED): compute BOTH.** 30d = headline (front-month, where VRP is most reliable + strikes liquid); 60d (≈VIX3M) = stability/confirmation. The 30↔60 divergence is itself a term-structure/calendar signal. `vol_richness.horizon_dte` sits in the natural key so multi-horizon is ~free. Rejected single-21d (sharper but no term read) and single-60d (fuzzier forecast + slower backtest validation from overlapping-window autocorrelation).

**Core edge:** `vrp_pts = IV_atm(h) − forecastRV(h)`, standardized to the name's own trailing percentile + IV rank. Trailing rv20/rv60 is the WRONG input — need a *forward* RV forecast (the missing half).

**Build order (Phase A — all descriptor, no signals):**
- [x] `prices/forecast_vol.py` — **DONE 2026-05-26.** HAR-RV (Corsi daily/weekly/monthly trailing-variance components, OLS via `np.linalg.lstsq`; daily realized variance proxied by squared daily log return since no intraday feed) + EWMA(0.94) baseline. Public API `forecast_vol(close, horizons=(30,60))` → `{dte: VolForecast(har_rv, ewma_rv, har_r2, n_obs)}`, annualized decimal; calendar→trading dte map (30→21, 60→41); HAR fit per horizon, EWMA horizon-independent (flat-forward); insufficient history → `har_rv=None` graceful. Reuses `realized_vol.log_returns`. 12 tests green, ruff clean.
- [x] `vol/richness.py` (new `vol/` package) — **DONE 2026-05-26.** `atm_iv_at_horizon` interpolates the `DeltaSurface.atm_iv` term structure to a horizon in **total-variance space** (constant-maturity, linear in iv²·t; clamps outside the tenor span). `compute_vrp` = iv_atm − forecast_rv; `percentile_rank` + `iv_rank` standardize to the name's own trailing history (`MIN_HISTORY=20` → cold returns `None`); `classify_richness` (rich ≥0.80 / cheap ≤0.20 / neutral / cold); `build_richness_row` + `rank_richness` (tidy frame, richest-first, cold sinks last). richness_score = VRP percentile. 12 tests green, ruff clean.
- [x] `vol/term_skew.py` — **DONE 2026-05-26.** `term_slope` (far−near, unit-agnostic) + `classify_slope` (contango/backwardation/flat, caller-supplied flat band: 0.005 decimal IV / 0.5 VIX pts); `vix_term_slope(vix9d, vix6m)`; `classify_skew` (steep/moderate/flat/inverted in pts, mirrors surface_report) + `skew_percentile` (reuses `richness.percentile_rank`); `classify_zone` + `RegimeGate`/`build_regime_gate` (VEGA/VIX zones: low<22 / mid 22-32 / high>32; short-vol allowed unless high; VIX-unknown → gate inactive, never fabricated); `is_short_vol_label` + `gated_label` (the mandatory tail-risk overlay — annotates rich/premium-sell labels "GATED OFF" in stress, leaves cheap/neutral/cold untouched; only ever tightens). **Decoupled from the DB layer:** mirrors the `vix_view` zone constants locally (with a sync note) so the pure analytic doesn't drag in sqlalchemy. 9 tests green, ruff clean.
- [x] Migration **`0013_vol_richness`** (NOT 0011 — head had advanced to 0012) — **DONE 2026-05-26.** `VolRichness` model in `models.py` + `alembic/versions/0013_vol_richness.py` (revision "0013", down_revision "0012"). Table `vol_richness` (symbol FK→tickers, ts[Date, trading day], horizon_dte, iv_atm, fcst_rv, vrp_pts, vrp_pctile, iv_rank, term_slope, skew_25d, regime_zone, richness_score, label), UQ `(symbol, ts, horizon_dte)`, index on `(symbol, ts)`. **UN-PRUNED** — doubles as the long IV/VRP percentile baseline (works around `oi_chain_eod`'s 90d retention). Reversible: verified upgrade/downgrade round-trip clean on SQLite via a live Alembic op context (14 cols, UQ, FK all present then dropped); model↔migration columns asserted equal; ruff clean. **Mithil: `alembic upgrade head` to apply 0013 on the real DB** (sandbox can't reach Postgres).
- [x] `scheduler/jobs/vol_richness.py` — **DONE 2026-05-26.** EOD, registered in `runner.py` at **16:40** (after `oi_chain_eod` 16:35). Reads STORED data only (no Convex/vendor, no `OptionsDataSource` arg → `run(session, *, settings, symbols=None)`): latest `oi_chain_eod` chain → `build_delta_surface` → `atm_iv_at_horizon` @30/60; `quotes_daily` close → `forecast_vol`; prior `vol_richness` rows (ts<today) → standardization history; latest `vix_data.vix` → `build_regime_gate`. Pure `build_rows()` returns the records; `_upsert()` does the idempotent `ON CONFLICT (symbol,ts,horizon_dte) DO UPDATE`. Term slope (iv60−iv30) shared across a name's rows; skew_25d picked at the expiry nearest each horizon; label is the gated richness label. 4 tests green (cold-start, warm-history→rich→GATED-OFF in stress, skip-no-chain, upsert compiles for pg). + NAS DSM task when deployed. **Sandbox note: full integration test only runnable via a heredoc-reconstructed clean tree — the mount reliably truncated `config.py`/`surface.py` + served a stale `models.pyc`; canonical files (Read/Edit/Write) are correct.**
- [x] `dashboard/vol_richness_data.py` + `dashboard/pages/9_Vol_Richness.py` — **DONE 2026-05-26.** Pure `vol_richness_data.py`: `load_latest` (rows from the most recent scan ts), `richness_sheet(horizon)` (richest-first, cold sinks last), `available_horizons`, `regime_caption` (from stored `regime_zone`), `DISPLAY_COLS`, `TAIL_RISK_NOTE` (mandatory caveat). 7 tests green, ruff clean. Thin `pages/9_Vol_Richness.py`: horizon radio, regime banner, sortable `st.dataframe` with per-column help, tail-risk caption, empty-state, how-to-read expander (lint-clean; runtime needs streamlit on the laptop). **Also: silenced the benign `Mean of empty slice` RuntimeWarning in `greeks/surface.py` `DeltaSurface.atm_iv` (wrapped nanmean in `warnings.catch_warnings`) — surfaced by the first live job run (122 rows / 61 symbols).**
- [ ] **Vol cone / expected-move envelope (companion deliverable — same inputs as the scanner).** Forward price-band cone `Band(T) = S × σ × √(T/365)` (±1σ / ±2σ rails fanning with √time) at **weekly / monthly / quarterly** horizons, drawn in THREE flavors: **implied** (ATM IV per expiry from `surface.py` / `vix_data` term structure), **realized** (rv20/HV — this is the "mechanical cone / path of least resistance if RV stays subdued" from market commentary), and **forecast-RV** (HAR from `forecast_vol.py` — sharper than either, which the commentary cones don't have). Overlay actual price path → **range-usage** (how much of ±1σ is spent: move spent vs trending vs compressed) + call/put walls as horizontal markers (cone = statistical envelope, walls = dealer-hedging levels; overlap = the interesting spot). Convex's **IP module** is its native implied-probability/expected-move band equivalent (verify module detail — didn't render in the glossary); we reconstruct from `volatility`/`front_volatility`/`back_volatility` to stay vendor-neutral + draw all 3 flavors. CAVEAT: ±1σ≈68% only under lognormal/constant-vol — fat tails mean the cone UNDERSTATES tail risk; descriptive only (rule 4), not a target.
- [ ] AM-report wiring — top-3 richest/cheapest into `build_am_context`.
- [ ] `scripts/backtest_vol_richness.py` — VALIDATION GATE: walk stored history, IV vs realized forward vol, variance-swap P&L proxy (haircut hard — no bid/ask in stored data), vs naive always-short-vol baseline + regime gate.
- [x] **CONVEX FIELD CHECK — RESOLVED 2026-05-24** (from the Convex data-parameter glossary, jmftattoo-foundryfutures Notion). **vomma IS available**: `vomma`, `vommaxoi` (vomma×OI), `vommaxvolm` (vomma×volume) in Options Parameters → pull it for vol-convexity / short-vol sizing via the `OptionsDataSource` Protocol + `clients/convex.py` (rule 1); no Black-Scholes needed. **speed is NOT exposed** (Convex tops out at vanna/charm/vomma — no speed/color/higher-order) → skip; only BS-compute if ever truly needed (MASTER_PLAN deleted ~500 lines of BS greek code — avoid). Also confirmed present + useful: `volatility`, `front_volatility`, `back_volatility` (per-option IV + term components → surface/cone), `theo` (theoretical price), `oi_ch` (already used). **GOTCHA:** one bad param 400s the whole chain — add `vomma` to `_CHAIN_PARAMS` behind the same graceful-fallback wrapper as `oi_ch`.

**Optional viz polish (NOT blocking; ideas only, from anvgun/Options_Analyzer + StrikeVision review 2026-05-24 — borrow patterns, NOT their code: both are yfinance+BS, strictly inferior to our Convex/pre-computed-greeks stack):**
- [ ] **Treemap** (Option-type → Expiry → Strike; size = volume, color = OI) — a genuine single-glance "where is the whole chain concentrated" view we don't have (our views are per-strike bars + the strike×time GEX heatmap). Candidate for the Flow or OI page.
- [ ] **DAOI diverging-bar layout** — delta-adjusted OI, calls green above axis / puts magenta below, with wall detection. We already have `greeks/walls.py` (gamma walls) + DEX; this is the *delta* lens + a cleaner layout. Mild incremental value.
- [ ] **Expected-range band** — volume-weighted mean strike ± 1σ of the volume distribution + current-price line; cheap descriptive overlay.
- REJECTED: StrikeVision's "directional-bias gauge (0–100)" *as an interpretation* — a bullish/bearish score from raw volume distribution is exactly the positioning signal rule 4 / FlashAlpha says has no edge. Borrow the gauge *widget* only if ever useful, never the call.

**Phase B (only if backtest shows edge):** promote logic → `strategies/vol_richness.py` (SignalGenerator — the only place allowed to write `signals`), gated by backtested thresholds + regime + combined with VIX/ATM-IV/credit. Document in `docs/playbooks/vol_richness.md` + an ADR.

**Static no-arb "arbitrage" detection (calendar/butterfly/PCP) DELIBERATELY DROPPED as a P&L source** — at EOD resolution it surfaces stale-quote/wide-spread artifacts, not tradeable edge. Keep only as an optional data-quality filter if at all.

**Known caveats:** single-name IV percentiles start COLD (R4 — index/ETF reads usable now off VIX history); P&L proxy is gross-of-cost; NO execution layer yet → sheet is decision-support for manual trading; short-vol tail risk is real, regime gate is non-optional.

**Status as of 2026-05-19 evening:**
- Supabase database live with 14-table schema (project: `wrjizvhwsotoeymyjrcu`)
- Ollama running locally with `qwen2.5:3b` + `nomic-embed-text` pulled
- Convex API verified working (`api.get_und(['SPY'])` returns price)
- GitHub repo `rammpatel2013-sudo/trading-intel` synced with main branch
- DO droplet provisioned (not yet deployed to — Phase 7)

---

## What's done

- ✅ Master plan written (`MASTER_PLAN.md`, v2 — Convex-primary architecture)
- ✅ Folder structure created
- ✅ Core rule docs in place: `README.md`, `CLAUDE.md`, this file, `DEPLOYMENT.md`
- ✅ Decision: Convex pro tier as primary data source (resolves 2/3 Project 4 pre-build gaps)
- ✅ Decision: PostgreSQL 16 + pgvector, no SQLite fallback
- ✅ Decision: Voyage-3 for embeddings
- ✅ Decision: Streamlit for dashboard, FastAPI deferred to Phase 6
- ✅ Decision: Schwab fully retired from daily path (parked credentials only)
- ✅ Schwab daily token-health check task: pending deletion (Mithil to decide)

## What's done (Phase 0 + Phase 1 day 1)

- [x] Repo created on GitHub, initial scaffolding pushed (36 files)
- [x] `pyproject.toml`, `docker-compose.yml`, `Dockerfile`, `alembic.ini`, `alembic/env.py` all in place
- [x] GitHub Actions CI workflow wired
- [x] LLM stack switched to **Ollama local** (free)
- [x] 7 Discord webhooks copied from schwab1 → trading-intel/.env
- [x] DO droplet provisioned (idle, awaits Phase 7)
- [x] Supabase project created (`wrjizvhwsotoeymyjrcu`) with pgvector extension enabled
- [x] DATABASE_URL points at Supabase Direct connection (`?sslmode=require`)
- [x] Alembic migration `0001_initial_schema.py` written and applied — 14 tables exist in Supabase
- [x] Python venv created, dependencies installed (`pip install -e ".[dev]"`)
- [x] `convexlib` installed via `pip install git+https://github.com/convexvalue/convexlib.git`
- [x] Ollama installed (v0.24.0); `qwen2.5:3b` + `nomic-embed-text` pulled
- [x] `LLM_DAILY_MODEL=qwen2.5:3b` set in `.env` (RAM constraint at 16 GB total)
- [x] **Smoke test passed:** `api.get_und(['SPY'])` returned `[['SPY', 734.29]]`
- [x] Convex password rotated post-leak (clean credentials in `.env`)
- [x] FRED_API_KEY filled in `.env`

## What's done (Phase 1 day 2 — 2026-05-21)

- [x] `trading_intel/clients/convex.py` — working `ConvexClient` (`chain`/`underlying`/`exposures`/`health`), only file importing convexlib
- [x] `trading_intel/greeks/exposures.py` — GEX/DEX/VEX/CHEX + ATM IV (revised formulas, see below)
- [x] `trading_intel/greeks/flip_point.py` — zero-gamma price via BS repricing + scipy brentq over ±10%
- [x] `trading_intel/memory/db.py` — `make_session_factory(settings)` DB wiring helper (new)
- [x] `trading_intel/errors.py` — `TradingIntelError` hierarchy (was referenced in CLAUDE.md but missing)
- [x] `trading_intel/scheduler/jobs/greeks_snapshot.py` — idempotent snapshot job; registered in `runner.py` at 06:45 ET
- [x] `tests/clients/test_convex.py` — 11 mocked tests, no network; pytest green
- [x] Job run verified: 13/13 written, flip points populate, magnitudes sane
- [ ] **Commit + push** (pending — diagnostic script `scripts/diag_convex.py` is throwaway, delete before commit)

## Phase 1 day 2 done-criteria — RESULT
- ✅ 13 rows in `greeks_snapshots` per run
- ⚠️ "SPY gex in billions" expectation was off — that refers to ALL-expiration/gross GEX. We pull `exps=(1,2,3)` (3 nearest expirations) and report NET (calls−puts), so SPY net ~$16M; SPX ~$1.55B. **Open: confirm net-near-term vs gross + dealer sign convention against ConvexValue's own GEX display.**
- ✅ pytest green
- ✅ No vendor code outside `clients/convex.py`

## Phase 1.5 (NEW — deploy collector to DO droplet for 24/7 data continuity)

**Decision recorded in `docs/decisions/ADR-001-split-collector-architecture.md`.** Splits the system: minimal data-collector container runs on DO droplet 24/7 ($12/mo); Ollama and dashboard stay on laptop.

**Cadence (locked):** every 30 min during US RTH only (9:30–16:00 ET), plus EOD snapshot 16:30 + VIX/VVIX pull 16:45 + AM check 06:45. ~182 snapshot rows per ticker per market day; ~46K rows/year/ticker. Comfortably under Supabase free tier.

- [ ] Write `docker-compose.collector.yml` (collector-only stack, no Ollama, no dashboard)
- [ ] Write `Dockerfile.collector` (slim image, ~200 MB)
- [ ] Write `trading_intel/scheduler/runner_collector.py` (registers data-pull jobs only)
- [ ] Provision DO droplet (Ubuntu 24.04, non-root user, firewall, Docker) — droplet already exists, harden it
- [ ] Set up systemd unit `trading-intel-collector.service`
- [ ] Configure `.env` on droplet (CONVEX_*, DATABASE_URL, DISCORD_*) — NO Ollama, NO Anthropic key
- [ ] Write `.github/workflows/deploy-collector.yml` (auto-deploy on push to main)
- [ ] First deploy + verify via `journalctl -u trading-intel-collector -f`
- [ ] Add weekly Mon 09:00 ET Discord ping "collector alive"

## Phase 1.5 done-criteria
- 24 hours of uptime confirmed
- ~182 new `greeks_snapshots` rows per market day appearing in Supabase
- No alerts firing yet (per FlashAlpha rule — collector is data-only)
- Discord weekly alive-ping working
- Total monthly cost: $12 (DO droplet only)

## Phase 1.5b — Research knowledge pipeline (NEW 2026-05-21)

**Built the Type-1 "methodology" knowledge layer** (extraction + tagging; embeddings/RAG deferred). Pivoted here from the 24/7 collector at Mithil's direction (knowledge eval + AM summary + live price ranked higher). Collector still wanted, just deferred.

- `memory/pdf_pipeline.py` — walks `research/`, extracts PDF (pypdf -> pdfplumber fallback) + docx (python-docx incl. tables), SHA-256 dedupe vs `documents`, idempotent. CLI: `python -m trading_intel.memory.pdf_pipeline [--limit N] [--kind methodology|research] [--model M]`.
- `synthesis/prompts.py` + `synthesis/tagging.py` — Ollama (LLMProvider) framework extraction -> `docs/playbooks/<slug>.md`; theme tagging -> `themes` + `theme_observations`. Defensive JSON parse + value clamping.
- Migration `0003` — `documents.kind` (`methodology` | `research`) + check + index; existing rows backfill `methodology`.
- Full ingestion run: **13/13 research docs** (9 PDF + 4 docx), 0 empty, 0 failed, on `qwen2.5:3b`. Long books (>14k chars) truncated to opening section (noted in each playbook).
- Playbooks are **local-only** (gitignored); Supabase `documents`/`themes`/`theme_observations` are the durable record.
- 25 tests green (11 new); ruff/black clean.

**Two knowledge types (locked 2026-05-21):**
- **Type-1 methodology** = knowledge FOR the LLM: frameworks applied to live Convex/vol data to find/interpret trades. <- this layer, now seeded.
- **Type-2 company research** = knowledge ABOUT companies/themes: symbol/theme material for deep research, watchlists, Q&A. Needs embeddings/RAG + symbol-keyed ingestion + a Q&A interface. NOT built; needs company source docs.

**Deferred / connected pieces (the supplementing roadmap):**
- 24/7 collector to DO droplet (ADR-001) — still wanted; series gaps accumulate until deployed. (runner_collector + Dockerfile.collector + deploy-collector.yml + gate old deploy.yml.)
- Type-2 RAG/Q&A/watchlist layer (embeddings into `chunks` pgvector).
- Convex-style dashboard view (joy-plot / gxoi-by-expiration) — Mithil's stated dashboard preference.
- AM summary (ties methodology + data + live price together).

## Data-gap analysis — research playbooks -> Convex data (2026-05-21)

> **[STALE as of 2026-05-24]** The jobs this section calls "not built" are now built (chain_snapshot, flow_snapshot, vix_snapshot, quotes_daily, oi_chain_eod). Kept for history; see the 2026-05-24 decision-log entry.

Derived from the 13 ingested methodology playbooks: maps each recurring framework
to the data it requires and our current collection state. Four tables already
exist (`greeks_chain`, `flow_buckets`, `vix_data`, `quotes_daily`) but NO job
writes them yet — that is exactly where the leverage is.

**What the research demands (recurring across playbooks):**
- Implied vol SURFACE (strike x expiry x IV) over time — ManagingSmileRisk (SABR),
  IV-Surface-Construction, Forecasting-IVS-dynamics, Riding-on-a-Smile, local-vol. The central object.
- Smile/skew (IV vs strike, 25-delta skew) + term structure (VXST/VIX/VXV/VXMT + ATM-IV per expiry).
- SABR params (alpha=vol-of-vol, beta, rho, nu) + vanna/volga, fit from the smile.
- Vol-of-vol / VVIX (How-vol-of-vol-depends-on-vol; VIX-options paper).
- Realized-vs-implied (IVAR) — Trading-Volatility; needs realized vol.
- GEX gamma regime: cumulative gamma by strike, flip, max pain, put-vs-call gamma (red/blue) — gex-explanation.
- flowratio / vflowratio + the 4 money/volume conditions + 5m/15m/30m bucketed flow — convex.docx.
- Market internals $ADD (NYSE adv minus decl), TICK — Trading-with-Market-Internal.
- Daily OHLCV -> 10wk/30wk MA, stochastic, realized vol — dr.wish, and the GEX:RVOL classifier.
- VIX / VVIX / MOVE / credit spreads — FlashAlpha probability model (Phase 5+).

**Gap table (what to collect next):**

| Need | In Convex? | Table (exists) | Job? | Action |
|---|---|---|---|---|
| Per-strike IV surface + greeks + gxoi over time | YES (chain returns it; we discard after aggregating) | `greeks_chain` | none | **#1: write a per-strike chain-snapshot job.** Unlocks surface/smile/skew/SABR/cumulative-gamma/max-pain/vol-of-vol. |
| flowratio/vflowratio + bucketed flow (5m/15m/30m) | YES (und flowratio/vflowratio; bucketed = chain params, not pulled yet) | `flow_buckets` | none | flow-snapshot job; add bucketed-flow params to the chain pull. |
| VIX, VVIX, MOVE, HY/IG OAS, VIX term structure | NO (options-only) | `vix_data` | none | FRED (key present) for VIX/MOVE/credit; CBOE scrape (`clients/cboe.py`, not built) for VVIX + term structure. |
| Daily OHLCV -> rv20/rv60, MAs, stochastic | NO (Convex gives spot only) | `quotes_daily` | none | yfinance fallback or Convex und history; compute RV -> enables IVAR + GEX:RVOL + dr.wish rules. |
| Market internals ($ADD, TICK, breadth) | NO | (new table) | none | needs external breadth source + new table; lower priority. |
| Skew/term-structure metrics, volga | DERIVED / verify | — | none | derive from `greeks_chain` once stored; verify `volga` exists in the convexlib field list. |

**Verify against the convexlib field list (do not assume):** exact names for the
time-bucketed flow params (volmbs/valuebs 5m/15m/30m), and whether Convex exposes
`volga` and VIX/VVIX directly. One bad param 400s the whole chain request (quirks).

**Priority for the next data session:** (1) per-strike `greeks_chain` collector —
biggest unlock, pure Convex, table ready; (2) `quotes_daily` OHLCV + realized vol;
(3) `vix_data` (FRED first, CBOE after); (4) `flow_buckets`. All FlashAlpha-safe
(data-only, no signals). When the 24/7 collector (ADR-001) is built, `runner_collector`
should register THESE jobs too, not just the current two.



---

## Open decisions (need answers before relevant phase)

| # | Decision | Default | Mithil's pick |
|---|---|---|---|
| 1 | Local-first 12wk → DO Phase 7, or DO from week 2? | Local-first | ✅ **Hybrid: collector to DO at Phase 1.5, dashboard local until Phase 7** (ADR-001) |
| 2 | DO Postgres: managed ($15/mo) or self-hosted on droplet? | Managed | ✅ **Neither — Supabase handles persistence**; collector droplet runs app only |
| 3 | Embedding provider | nomic-embed-text via Ollama (local, free) | ✅ Ollama / nomic-embed-text |
| 3b | LLM provider | Ollama local | ✅ `qwen2.5:3b` (RAM-constrained from 14b → 3b) |
| 4 | Schwab retention | Fully retire | ✅ Retire |
| 12 | Database hosting (local dev) | Supabase free tier | ✅ Supabase (free, no Docker overhead) |
| 5 | Watchlist scope (10 vs 50 vs dynamic) | Mag-7 + indexes (~10) | ? |
| 6 | AM summary delivery | Discord only | ? |
| 7 | FastAPI Phase 6 vs Streamlit-only | FastAPI Phase 6 | ? |
| 8 | Google Drive auto-pull for PDFs | Manual drop | ? |
| 9 | Keep ES futures flow page | Yes | ? |
| 10 | Domain name for DO hosting | — | ? |
| 11 | GitHub repo: private or public | Private | ? |

---

## Key facts to remember

### Data sources
- **ConvexValue (primary):** email/password auth (stable, no token refresh). Pro tier. Provides per-strike delta/gamma/theta/vega/vanna/charm + gxoi/dxoi/vxoi pre-computed + time-bucketed flow (5m/15m/30m).
- **FRED (macro):** free with API key, free tier sufficient.
- **SEC EDGAR (filings):** free, no key needed.
- **yfinance (price fallback):** unstable, use only as last resort.
- **CBOE (VVIX):** scrape from cboe.com — needs manual implementation in `clients/cboe.py`.
- **Schwab (parked):** OAuth, 7-day refresh tokens. NOT in daily path. Keep `.env` for the day account/portfolio integration is wanted.

### Watchlist (default)
`SPY, QQQ, SPX, AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, SMCI, PLTR` — 13 symbols, ported from schwab1 scheduler logs.

### Schedule (US Eastern times)
- 06:30 — news + earnings pull
- 06:45 — Greeks snapshot for watchlist
- 07:00 — AM summary + Discord send
- 09:30–16:00 every 5 min — live alerts
- 09:45 / 12:00 / 14:00 / 15:30 — intraday Greeks snapshots
- 10:00 / 14:00 — internals composite
- 16:30 — EOD snapshot + VVIX pull
- Sun 21:00 — weekly theme synthesis (Claude Opus)

### NAS deployment (how the collector runs) + lessons (2026-05-23) — read before redeploying
The NAS is a Synology (DSM, repo at `/var/services/homes/drmithil/trading-intel`, Postgres container `trading-intel-pg` on `192.168.1.211:5433`). How it actually runs — and what bit us:
- **The collector is NOT `runner.py`/APScheduler and NOT the docker-compose `scheduler` service.** It's DSM **Control Panel → Task Scheduler** tasks, each running `docker run --rm --network trading-intel-net -v .../.env:/app/.env -e DATABASE_URL=... trading-intel sh -c "python -m trading_intel.scheduler.jobs.<JOB> && ..."`. So **cron expressions in `runner.py` are ignored on the NAS** — every new job needs its own DSM task (script + schedule). To add a job: new User-defined-script task, user `root`, same docker wrapper, change the `jobs.<JOB>` module, set the Schedule.
- **The image bakes in code** (`COPY trading_intel ...`); only `.env` is mounted. Editing source on disk does nothing until the image is rebuilt. **Always rebuild with `docker build --no-cache`** — a plain build hit `Using cache` on the `COPY` layer and silently produced the identical image (timestamp never moved). Verify success two ways: Container Manager → Image timestamp flips to today **and** log shows `Successfully tagged trading-intel:latest`.
- **Git is NOT installed on the NAS** (`git: command not found`), and the repo dir may not be a real clone. Update code via **GitHub tarball overlay**, not `git pull`: `curl -sL -o ti.tar.gz https://github.com/rammpatel2013-sudo/trading-intel/archive/refs/heads/main.tar.gz; tar xzf ti.tar.gz; cp -rf trading-intel-main/. trading-intel/` (overlay preserves the gitignored `.env`), then `docker build --no-cache -t trading-intel ./trading-intel`. Repo is public so no auth needed.
- **Task stdout isn't visible in the DSM UI** — redirect to a log file in the home dir (`> /var/services/homes/drmithil/ti_update.log 2>&1`) and open it via File Station. Tasks run under **bash**.
- **`docker run --rm` one-shot jobs trigger a Container Manager "container <random-name> stopped unexpectedly" event on every successful exit.** Benign (jobs start→work→exit); `EXIT 0` is the real signal. The 5-min job will fire this notice ~78×/trading-day — silence via Control Panel → Notification if noisy.
- **Schema migrations are applied from the LAPTOP over the network** (DATABASE_URL → `192.168.1.211:5433`), independent of the image rebuild. DB schema and the collector image are updated separately. **NAS DB is at head `0010` as of 2026-05-24** — the NAS Postgres `192.168.1.211:5433/trading_intel` is the SINGLE operative DB (see the 2026-05-24 DB-topology note in the decision log).
- DSM schedule: "Run on **Weekly** (Mon–Fri)" + "First/Last run time" + "**repeat every N minutes**" models RTH cadence. **Times follow the NAS clock** — set to match US market hours.

### Dev-workflow gotchas (cowork sandbox ↔ Windows repo)
- **Can't `git push` from the sandbox** (no GitHub auth). Commits land locally in the mounted repo; **Mithil pushes** (PyCharm). Same for all live-infra steps.
- A stale `.git/index.lock` on the Windows-mounted repo **can't be `rm`'d** from the Linux sandbox (`Operation not permitted`) but **can be `mv`'d** aside. The same EPERM shows up as harmless `unable to unlink tmp_obj_*` warnings during `git add`/commit — objects still write; confirm with `git fsck --connectivity-only`.
- **Sandbox ruff (0.15.x) ≫ repo's pinned `ruff>=0.5`** and flags ~32 pre-existing UP007/RUF002/ANN204 issues the project treats as clean. **Don't fix repo-wide ruff drift — lint only changed files.** (`S105` on `SCHWAB_TOKEN_PATH` is a false positive — it's a file path.)

### Formulas (FINAL as of 2026-05-21 — see decision log)
**Units decision (validated against the ConvexValue app):** `gex_total` is **raw net signed gxoi** (calls +, puts −) — matches what Mithil sees in Convex's gxoi panels (e.g. AAPL ≈ 3.70k near-term). Convex's `gxoi`/`dxoi`/`vxoi` are already `greek × oi` per-share; we deliberately do NOT apply the contract multiplier or spot² dollar-scaling (we tried that — SpotGamma-style $ GEX — and dropped it to match the source).
- GEX = `Σ sign × gxoi` (net signed gxoi)
- DEX = `Σ dxoi` (dxoi already carries call/put sign)
- VEX (vanna) = `Σ vanna × oi × spot × IV`
- CHEX (charm) = `Σ charm × oi × spot × 365`
- GEX flip point = price where net GEX = 0, via BS-repricing each strike's gamma at candidate spot + scipy.optimize.brentq over ±10% of spot (returns None if no sign change in range). Uses Convex's `multiplier` field (constant scale, doesn't move the zero).
- GEX:RVOL ratio = `GEX / 20-day realized vol` (primary regime classifier — not yet implemented; needs quotes_daily.rv20)

### Rolling / long-dated GEX (NEW 2026-05-21)
For directional-flow tracking. EOD job `scheduler/jobs/gex_rolling.py` (registered 16:30 ET) pulls a wide chain (`chain_long` — wide exps with fallback) per watchlist symbol, filters to expirations within ~180 days, and stores:
- `gex_rolling` table — 6-month TOTAL net gxoi per symbol (the directional-flow time series)
- `gex_term` table — per-expiration net gxoi (term structure)
Both idempotent (ON CONFLICT), ts floored to the day. **Requires migration `0002_gex_rolling` to be applied (`alembic upgrade head`).** Near-term snapshot stays at exps=(1,2,3), rng=0.15; rolling uses rng=0.20.

### ConvexValue API quirks (learned the hard way 2026-05-21)
- Valid field codes are in the convexlib README. There is NO `cxoi` — charm×oi is `charmxoi`. One bad param 400s the whole request.
- `get_chain_as_rows` returns `[symbol, expiration, strike, kind, *params]` — symbol/expiration/strike/kind are STRUCTURAL, do not request them as params. `kind` is the literal `'call'`/`'put'`.
- `get_und` nests rows one level deeper than the README example: `{"data": [[ [symbol, *vals], ... ]]}` — rows live at `data[0]`.
- Time-bucketed flow (`volm_5m`, ...) are CHAIN params, NOT get_und params.
- `expiration` is days since the Unix epoch (e.g. `20595` = 2026-05-22). The convex client normalizes this to a real datetime so the greeks layer stays vendor-agnostic.

### VEGA/VIX zones
- Low <22 (carry)
- Mid 22–32 (fragility)
- High >32 (stress; crisis ≈ 38.3)

### Persistence decay (vol spikes)
- Days 1–2: fade edge high
- Days 3–5: edge decays
- Day 6+: persistence dominates, stop fading

### Thrasher signal thresholds (recalibrate before use!)
- VIX 20-day StdDev ≤ 0.86 (original 2017 threshold — recalibrate on 2020–2025)
- VVIX 20-day StdDev ≤ 3.16 (same — recalibrate)

---

## Data migration plan (legacy → new DB)

| Source | Destination | Status |
|---|---|---|
| `schwab1/flow_scan_history.json` (4.4 MB) | `signals` (signal_type=flow_scan, source=schwab_legacy) | ⏳ Phase 1 |
| `schwab1/iv_history.json` (751 KB) | `greeks_snapshots.atm_iv` | ⏳ Phase 1 |
| `schwab1/gex_history.json` (140 KB) | `greeks_snapshots` (source=schwab_legacy) | ⏳ Phase 1 |
| `schwab1/roadmap_gex_history.json` (508 KB) | `greeks_snapshots` (source=schwab_legacy_roadmap) | ⏳ Phase 1 |
| `schwab1/scheduled_gex_history.json` (212 KB) | `greeks_snapshots` (source=schwab_legacy_scheduled) | ⏳ Phase 1 |
| `schwab1/volume_snapshots.json` (228 KB) | `volume_snapshots` | ⏳ Phase 1 |
| `schwab1/signals_today.json` (14 KB) | `signals` | ⏳ Phase 1 |
| `schwab1/internals_data.json` + `internals_history.json` | new internals composite table | ⏳ Phase 1 |
| jdscan 9 PDFs (~14 MB) | `documents` + `chunks` (via pgvector) | ⏳ Phase 3 |
| jdscan daily scan CSVs | `data/snapshots/jdscan/` | ⏳ Phase 4 |

---

## Recent decisions / decision log

(Move to `docs/decisions/ADR-N-name.md` once an ADR is written. This is the short-form trail.)

**2026-05-24 (vol-richness scanner — PLANNED, not built)**
- New workstream scoped: rank options IV rich/cheap vs FORWARD realized vol (HAR forecast) → daily rich/cheap sheet for finding mispriced options. Full build checklist in the "Vol-richness scanner (PLANNED)" section near the top.
- HORIZON DECISION (locked): compute BOTH 30d (headline) + 60d (≈VIX3M confirmation); 30↔60 gap = calendar/term signal. Rejected single-21d and single-60d.
- Descriptor-first per rule 4: Phase A emits NO signals (pure analytic + dashboard, like `oi_changes`/`watchlist_metrics`); only `strategies/vol_richness.py` may write `signals`, after `scripts/backtest_vol_richness.py` proves edge. On-ramp to the Phase-5 probability model.
- `vol_richness` table intentionally un-pruned to serve as the long IV/VRP percentile baseline (works around `oi_chain_eod`'s 90d retention).
- No code written this session — plan only.

**2026-05-24 (VIX / volatility workstream)**
- VENDOR DECISION (locked — do not re-litigate): stay **Convex-only** for options. Evaluated **IBKR** (rejected: requires TWS/Gateway always-on + daily auto-logout) and re-evaluated **Schwab** (rejected for now: the 7-day refresh-token chore buys only intraday bars + the VIX complex, not needed while daily granularity suffices). Daily OHLCV via yfinance, VIX/MOVE/credit via FRED. Revisit Schwab ONLY if an intraday/low-timeframe use-case lands. MOVE + credit stay on FRED (not available on Schwab).
- Persisted VIX term structure + VRP (migration **0010**: `vix_data.vix9d/vix3m/vix6m/vrp`). `vix_snapshot` now stores the CBOE term structure it already fetched and computes `vrp = VIX − SPX rv20×100`. Verified live: vix9d 14.07 / vix 16.76 / vix3m 20.03 / vix6m 22.35 (healthy contango), vrp +6.01. CBOE CDN term endpoints (_VIX9D/_VIX3M/_VIX6M) confirmed working.
- VIX dashboard (`8_VIX.py`) enhanced: VVIX/VIX + VIX9D/VIX metrics, stored term-structure curve classified contango/backwardation/flat, VRP trend chart, "how to read" expander. New pure helpers in `vix_view.py`.
- VIX DECOMPOSITION (CBOE 6-factor) replicated. Feasibility: no CBOE API/feed (interactive web tool only) → replicate from our data per the CBOE whitepaper (`VIX-Decomposition-2025-08-01.pdf`). `greeks/vix_decomposition.py` (pure: sticky strike / parallel shift / put+call gradient @30Δ / down+up convexity @10Δ on a synthetic 30-day fixed-strike skew). Validated vs CBOE's Yen-Carry worked example (2.57/7.29/1.66/3.43). Source = `oi_chain_eod` (SPX per-strike iv+delta, ~30d expiries); needs ≥2 consecutive SPX EOD snapshots. Loader `dashboard/vix_decomp_data.py` + panel on the VIX page (shows "accumulating history" until 2 snapshots; SPX had 1 as of 2026-05-23, 14.7k rows, full iv+delta). LITE attribution (representative-delta IV excess); FULL (VIX variance-strip recompute) is a later refinement.
- Interpretation guide: `docs/guides/reading-the-vix.md` (term structure, VRP, VVIX, the 6-factor decomposition, how it feeds a swing bias).
- The **Data-gap analysis (2026-05-21)** section below is STALE: the jobs it calls "not built" are all built now (chain_snapshot, flow_snapshot, vix_snapshot, quotes_daily, oi_chain_eod). Kept for history.
- SANDBOX CAVEAT (Cowork/agent): the Linux sandbox mount TRUNCATES files with multibyte chars (em-dash, box-drawing) — it mangled `vix_view.py` and couldn't import `models.py`. Authoritative = the Windows file tools; run tests on the real machine. ASCII-only modules run fine in the sandbox.
- DB TOPOLOGY (clarified 2026-05-24): the **NAS Postgres `192.168.1.211:5433/trading_intel` is the single operative DB**, now at head `0010`. The laptop `.env` had a *duplicate* `DATABASE_URL` (a Supabase URL + the NAS URL); with `.env` last-wins parsing the NAS line was always effective, so all of this session's migrations + data went to the NAS DB — NOT Supabase (a mid-session guess that it hit Supabase was wrong). Supabase retired (commented out); the "Supabase is persistence" plan (decisions #2/#12) is superseded. Rationale: per-strike chain volume (GB/yr) suits the local NAS PG; remote dashboard access, if ever needed, via Tailscale into the LAN, not a cloud DB. (Still: rotate the Supabase password that was pasted in chat.) Remaining NAS-side ops: rebuild image from pushed code (so scheduled `vix_snapshot` fills the new term-structure/VRP columns), confirm `oi_chain_eod`/`vix_snapshot` DSM tasks exist.
- NEXT: gamma-regime classifier (#10, from `oi_chain_eod` cumulative gamma + flip); then FMP market-internals (free tier) + market-timing dashboard; vol lab; swing-trade synthesis. **dr.wish engine dropped per Mithil.**

**2026-05-23 (Phase 2.1 — daily AM report)**
- Built the AM report as a SUMMARIZER over stored data, not a collector — it reads what the greeks/chain/flow/intraday/quotes jobs wrote. Running it anytime refreshes today's row; it reads ~30d history + last-7d for ΔGEX. It always reports for `datetime.now().date()` — `build_am_context` accepts an `as_of` for backfill, but the job entrypoint doesn't expose a `--date` flag yet.
- Daily LLM = local Ollama (`LLM_DAILY_MODEL`) per rule 7, with a deterministic tables-only fallback so the row always writes even if Ollama is down. `claude_model` records the local model name (null on fallback); `tokens_used` left null on the Ollama path.
- Discord delivery deferred (no `clients/discord.py`); gated behind `AM_REPORT_SEND_DISCORD` (default off) as a no-op stub. Mithil's stated goal was "see it on the dashboard," so dashboard-first.
- Upsert quirk: insert against `AmSummary.__table__` (not the ORM class) — the column is literally named `metadata`, which shadows SQLAlchemy's `MetaData` on the mapped class, so `pg_insert(AmSummary).values(metadata=...)` raises `AttributeError`. `on_conflict_do_update` compiles fine on SQLite for tests.

**2026-05-21 (Phase 1.5b — research knowledge pipeline)**
- Pivoted from the 24/7 collector to the research knowledge pipeline (Mithil's priority). Collector still wanted; deferred.
- Split the KB into two kinds: methodology (knowledge FOR the LLM, applied to live data to find/interpret trades) vs research (knowledge ABOUT companies/themes: deep research, watchlists, Q&A). Encoded as `documents.kind` (migration 0003).
- Built the methodology layer only: frameworks -> `docs/playbooks/*.md` + theme tagging -> DB. Embeddings/RAG deferred (it is the substrate for the Type-2 research layer).
- Playbooks kept local-only (gitignored); Supabase rows are the durable record.
- LLM: Ollama `qwen2.5:3b` (LLM_DAILY_MODEL); quality good. 13/13 docs ingested, 0 failed.

**2026-05-21 (Phase 1 day 2 build)**
- Flip-point method: chose **BS repricing** (recompute each strike's Black-Scholes gamma at candidate spot, sum sign-weighted dollar-gamma, brentq for zero) over lightweight strike-profile interpolation. More accurate; matches the "±10% range" intent.
- Formula revision: discovered `gxoi`/`dxoi`/`vxoi` are per-share (no ×100 multiplier). Added contract multiplier `m` to all dollar exposures. Now use Convex's `multiplier` field per row (default 100). Validated: SPX ~$1.55B, SPY net ~$16M near-term.
- Added `memory/db.py` (session factory) and `errors.py` (TradingIntelError hierarchy — CLAUDE.md referenced it but it didn't exist).
- OPEN calibration question: net-near-term GEX (current: `exps=(1,2,3)`, calls−puts) vs all-expiration/gross, and which dealer sign convention. Compare to ConvexValue's own GEX display before trusting absolute magnitudes for signals.

**2026-05-19**
- Convex pro tier chosen over Schwab as primary data source. Rationale: no 7-day token refresh; pre-computed vanna/charm; cleaner data shape. Trade-off: vendor lock-in (mitigated by `OptionsDataSource` Protocol).
- Schwab fully retired from daily path. Existing `schwab1/token.json` + `.env` kept in case of later portfolio-integration need.
- Scaffolding created inside `schwab1/trading-intel/` as starter. To be moved to its own folder at `C:\Users\drmit\PycharmProjects\trading-intel\` once Phase 0 begins.
- **Kalman Filter research captured.** Article by @phosphenq on hedge-fund use of Kalman for vol tracking, dynamic beta, and order-book imbalance. Full notes at `docs/learning/kalman-filter.md`. Decision: NOT to implement now — too early. Apply in Phase 4 (JD pair-trading β sizing), Phase 5 (Thrasher signal, GEX:RVOL ratio denominator, Spot Up + Vol Up anomaly detection), Phase 6 (earnings-ripple dynamic correlations). Concrete tasks listed in the learning note.

**2026-05-19 (earlier)**
- Schwab token re-auth completed (was expired since March). SPY API call verified at $738.89.
- Daily 7AM Cowork token-health check scheduled. Will be deleted once Schwab is officially out of the daily path.

---

## Known issues / risks (live tracker)

| # | Issue | Severity | Status |
|---|---|---|---|
| R1 | FlashAlpha rule — no Greek-only signals before Phase 5+ | HIGH | Enforced in CLAUDE.md |
| R2 | Convex vendor dependency | MEDIUM | Mitigated by Protocol |
| R3 | Convex subscription cost (recurring) | LOW-MED | Calendar reminder needed |
| R4 | Cold-start: probability layer needs 4–8 wk of tagged data | MEDIUM | Backfill 2020–2025 in Phase 1 |
| R5 | PDFs may contain proprietary material | MEDIUM | `documents.source='internal'` flag, never export |
| R6 | Convex rate limits + concurrency | MEDIUM | Cache + batch — Phase 1 design |
| R7 | yfinance instability | LOW | Fallback only, degrade gracefully |
| R8 | Knowledge debt (FWDVOL, autocallables, etc.) | LOW | Phase 6+ |
| R9 | Heatmap simulation cost | MEDIUM | Cache per-ticker, recompute on chain refresh |

---

## Cost tracker

| Item | Cost / mo | Status |
|---|---:|---|
| ConvexValue Pro | TBD | Already subscribed |
| Anthropic API (Claude) | est. $5–20 | not yet enabled |
| Voyage embeddings | est. <$1 | not yet enabled |
| FRED, SEC EDGAR | $0 | not yet enabled |
| DO droplet | $12 | Phase 7 |
| DO managed Postgres | $12 | Phase 7 |
| DO Spaces (optional backups) | $5 | Phase 7+ |
| Domain (annual / 12) | ~$1 | Phase 7 |
| **Total at full deploy** | **~$30–45 + Convex** | Phase 7+ |

---

## Update protocol

At the end of every working session:
1. Move items from "What's next" to "What's done" as they complete
2. Add new items to "What's next"
3. Log significant decisions in "Recent decisions"
4. Update "Open decisions" table when answers come in
5. Update "Known issues" status column
6. Commit with message starting `memory: ...`
