# MEMORY.md — Working memory for trading-intel

Living document. Update at the end of every working session. Tells future-you (and any AI assistant) where things stand and what's next.

---

## Current phase

**Phase 1 day 1 done — moving to Phase 1 day 2.** Foundation complete, all external dependencies verified end-to-end.

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

## What's next (Phase 1 day 2)

- [ ] Build proper `trading_intel/clients/convex.py` — `ConvexClient` class implementing `OptionsDataSource` protocol with methods: `chain()`, `underlying()`, `exposures()`, `health()`. Replace the existing skeleton with a working implementation.
- [ ] Write `trading_intel/greeks/exposures.py` — compute GEX/DEX/VEX/CHEX from a chain DataFrame using locked formulas (see "Key facts" section below).
- [ ] Write `trading_intel/greeks/flip_point.py` — find price where net GEX crosses zero using scipy.optimize.brentq.
- [ ] Write `trading_intel/scheduler/jobs/greeks_snapshot.py` — pulls watchlist Greeks from Convex, aggregates, writes to `greeks_snapshots` table. Idempotent: `INSERT ... ON CONFLICT DO NOTHING` on `(symbol, ts, source)`.
- [ ] Manually trigger the job once: `python -m trading_intel.scheduler.jobs.greeks_snapshot` — verify rows appear in Supabase Table Editor.
- [ ] Add basic test: `tests/clients/test_convex.py` — mocked test of the `OptionsDataSource` interface.
- [ ] Commit + push.

## Phase 1 day 2 done-criteria (go/no-go)
- Manually run the greeks_snapshot job → 13 new rows in `greeks_snapshots` (one per watchlist ticker)
- Spot check: SPY's gex_total should be in the billions (positive or negative — both valid regimes)
- `pytest` still green
- No vendor-specific code outside `clients/convex.py`

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

### Formulas (locked in)
- GEX = `gxoi × spot² × 0.01` (calls +, puts −)
- DEX = `dxoi` (sum)
- VEX (vanna) = `vanna × oi × spot × IV`
- CHEX (charm) = `charm × oi × spot × 365`
- GEX flip point = price where net GEX = 0 (scipy.optimize.brentq)
- GEX:RVOL ratio = `GEX / 20-day realized vol` (primary regime classifier)

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
