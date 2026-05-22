# MEMORY.md — Working memory for trading-intel

Living document. Update at the end of every working session. Tells future-you (and any AI assistant) where things stand and what's next.

---

## Current phase

**Phase 1 day 2 DONE (2026-05-21) — moving to Phase 1.5 / calibration.** First real ingestion pipeline live: watchlist Greeks snapshot pulls from Convex and writes to `greeks_snapshots`. Verified end-to-end (13/13 tickers written, pytest green, flip points populating).

**Status as of 2026-05-21:**
- `greeks_snapshot` job runs clean: 13 rows/run, GEX/DEX/VEX/CHEX + flip point + ATM IV per ticker
- Sanity check passed: SPX GEX ~$1.55B, single names $20–190M, SPY net ~$16M (calls/puts near-cancel intraday). Flip points populate for 12/13 (SMCI null — no zero-crossing in ±10%, plausible)
- See decision log (2026-05-21) for the ConvexValue response-shape gotchas + formula revision

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
