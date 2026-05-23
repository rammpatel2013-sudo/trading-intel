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
- **Analytics** `dashboard/oi_changes.py` (pure): `load_recent_eod`, `build_oi_change_frame` (outer-join on expiry/strike/cp; new strikes treated as 0→today), `summarize_oi_change` (descriptive note), `top_oi_changes`. **Page** `dashboard/pages/7_OI_Flow_Change.py`: ΔGEX metrics, diverging per-strike ΔGEX bar, biggest-changes table, read-through caption; empty-state until ≥2 EOD snapshots.
- **Verified:** 8 new tests pass (collector window/field mapping + analytics diff on SQLite) + 2 isolated tests for the Convex happy/fallback paths; ruff clean on the changed files that the mount would serve (oi_changes, both test files, collector, prune, migration). `convex.py`/`runner.py`/`models.py`/`config.py` couldn't be lint-run via the mount (severe stale/truncated reads this session — see gotcha) but carry only small pattern-mirroring changes.
- **NAS deploy (Mithil):** `alembic upgrade head` from the laptop (applies 0009 to the NAS DB) + add DSM tasks for `oi_chain_eod` (~16:35) and `prune_oi_chain` (daily), same docker wrapper. **Also still pending: chain_snapshot/greeks_snapshot have NO NAS DSM task** (only intraday/flow/daily-prices run there) — that's why the GEX surface and this OI study won't accumulate until those EOD collectors are scheduled too.

**Open viz items (next — see `docs/NEXT_SESSION.md` for the detailed plan):**
- **Improve the fixed-strike vol visualization** (Ticker page `load_fixed_strike_changes` chart — readability/redesign; not yet addressed). [Track 2]
- **VIX dashboard** — needs the `vix_data` collector built first (FRED for VIX/MOVE/credit; CBOE scrape for VVIX + term structure VXST/VIX/VXV/VXMT), then a page with the zones (<22 carry / 22–32 fragility / >32 stress).

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
- **Schema migrations are applied from the LAPTOP over the network** (DATABASE_URL → `192.168.1.211:5433`), independent of the image rebuild. DB schema and the collector image are updated separately. NAS DB is at head `0008`.
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
