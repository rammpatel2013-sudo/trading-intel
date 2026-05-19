# Trading Intel — Master Merge & Build Plan (v2: Convex-primary)

**Goal:** Consolidate `schwab1`, `jdscan`, `convex` + the institutional research vision (Project 4) into a single system: **trading-intel**. Built for cross-theme stock research, GEX/DEX/VEX/CHEX surfaces, earnings ripple analysis, and a daily 7 AM AM-summary intelligence brief.

**Owner:** Mithil
**Primary data source:** ConvexValue (pro tier) — pre-computed Greeks + flow exposures, stable auth, no 7-day token nonsense
**Hosting target:** Local-first development → Digital Ocean droplet ($20–40/mo) once stable
**Timeline:** ~12 weeks, 7 phases

**Major architectural decision (v2):** Convex replaces Schwab as the primary data layer. Schwab integration is **optional/deferred** — only useful later if portfolio/account data is wanted. The 7-day re-auth pain goes away.

**Why this is good:** Convex resolves THREE of Project 4's three pre-build gaps in one move:
1. ~~Schwab options Greeks~~ → not needed
2. ~~Barchart Vanna/Charm per strike~~ → Convex provides directly
3. VVIX daily pull → still pending (CBOE — small scrape, low effort)

**Trade-off:** Convex pro tier subscription cost; vendor lock for primary data. Mitigation: build the data layer with an abstract interface so a future Schwab/Barchart/Tradier source can slot in.

---

## 1. Target folder structure

```
trading-intel/
├── .env                        # all secrets (gitignored)
├── .env.template               # checked in, no values
├── .gitignore
├── README.md                   # this plan, condensed
├── pyproject.toml              # poetry or uv
├── docker-compose.yml          # postgres + pgvector + app
├── Dockerfile
├── alembic/                    # DB migrations
│
├── trading_intel/              # source package
│   ├── __init__.py
│   ├── config.py               # env loader, typed settings (pydantic-settings)
│   │
│   ├── clients/                # external data clients
│   │   ├── convex.py           # ✅ PRIMARY — pre-computed Greeks + flow
│   │   ├── claude.py           # Anthropic Claude API
│   │   ├── fred.py             # macro data (free)
│   │   ├── sec_edgar.py        # filings (free)
│   │   ├── yfinance_wrap.py    # price fallback (unstable)
│   │   ├── cboe.py             # VVIX daily pull (scrape)
│   │   ├── schwab.py           # 🟡 OPTIONAL — kept for future portfolio/account
│   │   └── discord.py          # webhook alerts
│   │
│   ├── greeks/                 # the math layer (now thin — Convex pre-computes most)
│   │   ├── chain_parser.py     # Convex chain → normalized dataframe
│   │   ├── exposures.py        # aggregate gxoi/dxoi/vxoi/cxoi into GEX/DEX/VEX/CHEX
│   │   ├── flip_point.py       # GEX = 0 crossover (brentq)
│   │   ├── regime.py           # GEX:RVOL ratio, VEGA/VIX zones
│   │   └── heatmap.py          # 2D (spot%, IV-shock) simulation surface
│   │
│   ├── strategies/             # signal generators
│   │   ├── jdintown.py         # ports jdintown_scanner2.py
│   │   ├── internals_composite.py  # ports internals_scheduler.py (data adapted to Convex)
│   │   ├── options_flow.py     # ports options_flow.py (data adapted to Convex)
│   │   ├── fib.py              # ports fib_engine.py + fib_ruler_component.py
│   │   ├── thrasher.py         # VIX dispersion signal (new)
│   │   ├── confluence_volspike.py  # 5-condition vol spike (new)
│   │   └── earnings_ripple.py  # ripple effect engine (new)
│   │
│   ├── memory/                 # 3-layer knowledge store
│   │   ├── db.py               # SQLAlchemy engine, sessions
│   │   ├── models.py           # ORM models (see §2 schema)
│   │   ├── vector_store.py     # pgvector ops
│   │   ├── pdf_pipeline.py     # ingest → extract → tag → embed
│   │   └── tagging.py          # theme + scope + sentiment tagger (Claude)
│   │
│   ├── synthesis/              # LLM intelligence
│   │   ├── am_summary.py       # 7 AM brief generator
│   │   ├── prompts.py          # all Claude prompts in one place
│   │   ├── anomaly_detector.py # 7 anomaly checks (see §5)
│   │   └── readthrough.py      # cross-ticker earnings classification
│   │
│   ├── scheduler/              # APScheduler jobs
│   │   ├── runner.py
│   │   └── jobs/
│   │       ├── news_pull.py            # 6:30 AM
│   │       ├── greeks_snapshot.py      # 6:45 AM (Convex pull + aggregate)
│   │       ├── am_summary.py           # 7:00 AM
│   │       ├── internals.py            # intraday
│   │       ├── live_alerts.py          # intraday
│   │       └── vvix_pull.py            # end of day
│   │
│   ├── dashboard/              # Streamlit UI
│   │   ├── Home.py             # entry: AM summary widget
│   │   └── pages/
│   │       ├── 1_Per_Ticker.py        # ABBV-style 4-panel
│   │       ├── 2_GEX_VEX_Heatmap.py
│   │       ├── 3_Macro_Themes.py      # pgvector search
│   │       ├── 4_Earnings_Ripple.py
│   │       ├── 5_JD_Scan.py
│   │       ├── 6_Options_Flow.py
│   │       ├── 7_Alerts_Log.py
│   │       └── 8_System_Health.py
│   │
│   └── api/                    # FastAPI (Phase 6+)
│       ├── main.py
│       └── routes/
│
├── data/                       # local data root (gitignored)
│   ├── pdfs/                   # broker reports, transcripts, 10-Ks
│   ├── snapshots/              # daily JSON snapshots (L3 memory)
│   └── cache/                  # transient API caches
│
├── docs/
│   ├── frameworks/             # Thrasher, FlashAlpha, nextSignals notes
│   ├── playbooks/              # ported from jdscan PDFs
│   └── decisions/              # ADRs
│
├── scripts/
│   ├── migrate_history.py      # JSON → Postgres backfill
│   ├── ingest_pdfs.py          # batch PDF ingestion
│   └── recalibrate_thrasher.py # 2020–2025 threshold fit
│
└── tests/
```

---

## 2. Data layer design

### Database stack
- **PostgreSQL 16** with **pgvector** extension. Single instance handles structured (L2) + vector (L1).
- **Local dev:** Docker-compose (`postgres:16` + `pgvector/pgvector:pg16`)
- **DO production:** Managed Postgres ($15/mo) or self-hosted on droplet
- No SQLite fallback — bifurcated storage paths waste time.

### Core schema

| Table | Purpose | Key columns |
|---|---|---|
| `tickers` | Symbol master | `symbol PK`, `name`, `sector`, `industry`, `gics_id`, `is_active` |
| `quotes_daily` | EOD OHLCV + RVOL | `symbol`, `date`, `o/h/l/c/v`, `rv20`, `rv60` |
| `greeks_snapshots` | Per-ticker aggregate exposures time series | `symbol`, `ts`, `spot`, `gex_total`, `dex_total`, `vex_total`, `chex_total`, `gex_flip`, `gex_rvol_ratio`, `atm_iv`, `source` |
| `greeks_chain` | Per-strike (heavier, snapshot 1/day + intraday key strikes) | `symbol`, `ts`, `expiry`, `strike`, `cp`, `oi`, `volume`, `delta`, `gamma`, `theta`, `vega`, `vanna`, `charm`, `iv`, `gxoi`, `dxoi`, `vxoi`, `cxoi` |
| `flow_buckets` | Convex time-bucketed flow (5m/15m/30m windows) | `symbol`, `ts`, `bucket_min`, `volm`, `value`, `volmbs`, `valuebs`, `flowratio` |
| `vix_data` | VIX + VVIX + MOVE + credit spreads | `date`, `vix`, `vvix`, `move`, `hy_oas`, `ig_oas`, `vix_sd20`, `vvix_sd20`, `vega_zone` |
| `earnings_events` | Earnings + ripple metadata | `symbol`, `date`, `time`, `actual`, `estimate`, `surprise_pct`, `read_through_class`, `peer_impacts JSONB` |
| `themes` | Macro/sector/company theme tags | `id PK`, `name`, `scope ENUM`, `parent_id` |
| `theme_observations` | Tagged datapoints | `id`, `theme_id FK`, `symbol`, `date`, `sentiment`, `source_doc_id`, `quote_text`, `confidence` |
| `documents` | PDF + transcript registry | `id`, `path`, `source`, `type`, `ingested_at`, `sha256`, `page_count` |
| `chunks` | Vector-store chunks | `id`, `document_id FK`, `chunk_idx`, `text`, `embedding VECTOR(1024)`, `theme_ids ARRAY`, `symbols ARRAY`, `date` |
| `signals` | All triggered signals | `id`, `ts`, `symbol`, `signal_type`, `payload JSONB`, `confidence` |
| `alerts_sent` | Delivery log | `signal_id FK`, `channel`, `sent_at`, `response_code` |
| `am_summaries` | Daily 7 AM brief log | `date PK`, `markdown`, `metadata JSONB`, `claude_model`, `tokens_used` |

**pgvector index:** `chunks.embedding USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);`

### Migration of existing JSON history

| Source file | Size | Target | Notes |
|---|---:|---|---|
| `flow_scan_history.json` | 4.4 MB | `signals` (type=flow_scan) | Preserve original timestamps; tag `source=schwab_legacy` |
| `iv_history.json` | 751 KB | `greeks_snapshots.atm_iv` | Inspect — likely per-ticker IV time series |
| `gex_history.json` | 140 KB | `greeks_snapshots` | Map fields, `source=schwab_legacy` |
| `roadmap_gex_history.json` | 508 KB | `greeks_snapshots` | `source=schwab_legacy_roadmap` |
| `scheduled_gex_history.json` | 212 KB | `greeks_snapshots` | `source=schwab_legacy_scheduled` |
| `volume_snapshots.json` | 228 KB | New `volume_snapshots` table | Likely intraday volume profile |
| `signals_today.json` | 14 KB | `signals` | Append |
| `internals_data.json` + `internals_history.json` | ~3 KB | New internals composite table | Map |

Backfill script: `scripts/migrate_history.py` — idempotent (`INSERT ... ON CONFLICT DO NOTHING` keyed by `(symbol, ts, source)`).

---

## 3. Module disposition matrix

### convex (now PROMOTED to foundation)

| File | Disposition | Becomes |
|---|---|---|
| `advanced_gamma_scanner.py` | **REFACTOR (foundation)** | `clients/convex.py` (the chain/und/flow methods) + `greeks/exposures.py` (the gamma-wall computation) + `strategies/options_flow.py` (smart money flow) |
| `gamma_uoa_scanner.py` | **REFACTOR — merge into options_flow.py** | merged |
| `es_flow_viz.py` | **PORT** | `dashboard/pages/9_ES_Flow.py` (futures-flow page) |
| `create_heatmaps.py` + `create_heatmaps1.py` | **PORT** — the vanna/charm extraction is exactly what `greeks/heatmap.py` needs | `greeks/heatmap.py` |
| `gamma_dashboard.py`, `options_dashboard.py`, `streamlit_app.py` | **CANNIBALIZE** — best visuals lifted into new pages | distributed across `dashboard/pages/*` |
| `run_scanner.py` | **RETIRE** — replaced by APScheduler | dead |
| `test_connection.py` / `test_connection1.py` / `debug_api.py` | **PORT to tests/** | `tests/clients/test_convex.py` |
| `examples.py` | **AUDIT** — interesting patterns lifted; rest retired | partial |
| `config.py` / `config_template.py` | **RETIRE** — replaced by pydantic-settings + `.env` | dead (**also: rotate the Convex password — it's in plaintext config_template.py**) |
| `COMMANDS.md`, `HEATMAP_GUIDE.md`, `QUICK_START.md`, `README.md` | **MERGE into docs/** | docs |
| `output/` folder | **ARCHIVE** to `data/snapshots/convex_legacy/` | reference |

### schwab1 (strategy code kept, Schwab-data plumbing demoted)

| File | Disposition | Becomes |
|---|---|---|
| `options_flow.py` (261 KB) | **REFACTOR** — port the scanner logic; rewrite data layer against Convex | `strategies/options_flow.py` + `dashboard/pages/6_Options_Flow.py` |
| `options_flow_additions.py` | **MERGE into options_flow.py refactor** | merged |
| `internals_scheduler.py` (54 KB) | **REFACTOR** — composite-internals logic kept; replace Schwab calls with Convex/FRED | `strategies/internals_composite.py` + `scheduler/jobs/internals.py` |
| `schwab_dashboard.py` (373 KB) | **CANNIBALIZE** for visual layouts — data layer rewritten | split into `dashboard/pages/*` |
| `gex_scheduler.py` (87 KB) | **RETIRE most of it** — Convex pre-computes GEX. Keep only scheduling skeleton and any unique aggregation logic | shrunk into `scheduler/jobs/greeks_snapshot.py` (much smaller) |
| `schwab_auth_setup.py` | **OPTIONAL** — port only if Schwab kept for account/portfolio later | `scripts/auth_setup_schwab.py` (parked) |
| `discord_notifier.py` | **PORT** | `clients/discord.py` |
| `quant.py` (57 KB) | **AUDIT then REFACTOR** | likely splits into `strategies/*` |
| `calude1.py`, `claude.py`, `chatgpt.py` | **RETIRE** prototypes; salvage best prompts | best content → `synthesis/prompts.py` |
| `mutiticker.py`, `oversold.py`, `streaming.py` | **AUDIT individually** | small utilities |
| `signals_dashboard.html`, `internals_dashboard.html` | **REGENERATE** from new dashboard | not source |
| `Dockerfile`, `docker-compose.yml`, `requirements.txt` | **REPLACE** with new pyproject.toml + new docker stack | new |
| Token health check Cowork task | **DELETE** (or pause) — no longer needed | gone |
| `token.json`, `.env` Schwab keys | **ARCHIVE** — keep `.env` for the day Schwab is reintroduced, but it's no longer in the daily path | archived |

### jdscan (kept largely intact — strategy + alerts code is data-layer-agnostic)

| File | Disposition | Becomes |
|---|---|---|
| `jdintown_scanner2.py` (45 KB) | **REFACTOR (v2 only)** — data layer adapted to Convex underlying-quote endpoint | `strategies/jdintown.py` |
| `jdintown_scanner.py` (v1) | **RETIRE** | dead |
| `dashboard_v3.py` (32 KB) | **REFACTOR** — v3 only | `dashboard/pages/5_JD_Scan.py` |
| `dashboard.py`, `dashboard_v2.py` | **RETIRE** | dead |
| `fib_engine.py` | **PORT** | `strategies/fib.py` |
| `fib_ruler_component.py` | **PORT** | `dashboard/components/fib_ruler.py` |
| `live_alerts.py` | **REFACTOR** — data calls swapped to Convex | `scheduler/jobs/live_alerts.py` |
| `live_alerts_state.json` | **MIGRATE** to DB-backed state | gone |
| `data_updater.py` | **REFACTOR** as a scheduler job | `scheduler/jobs/data_update.py` |
| `jdintown_excel.py` | **PORT** as optional exporter | `reports/jd_excel.py` |
| `schwab_data.py` | **RETIRE** — Convex client takes its place | dead |
| `schwab1/` subfolder | **DELETE** | dead |
| Strategy PDFs (~14 MB) | **INGEST** via pdf_pipeline.py | embedded in `chunks` |
| MEMORY.md, README.md | **MERGE into docs/playbooks/** | docs |
| Daily scan CSVs | **ARCHIVE** to `data/snapshots/jdscan/` | reference |

---

## 4. Unified Convex data client

**Replaces:** the 4 duplicate Schwab auth/chain implementations (schwab1's `schwab_auth_setup.py`, `gex_scheduler.py`, `options_flow.py`, and jdscan's `schwab_data.py`) — all of which get retired.

**Proposed surface (`clients/convex.py`):**

```python
class ConvexClient:
    def __init__(self, settings: Settings)
        # uses convexlib.api.ConvexApi internally

    # Chain data with pre-computed Greeks
    def chain(self, symbol: str, *, exps=(1, 2, 3), strike_range=0.15) -> pd.DataFrame
        # Returns: strike, expiration, opt_kind, delta, gamma, theta, vega, vanna, charm,
        # iv, oi, volume, gxoi, dxoi, vxoi, cxoi

    # Underlying-level flow + exposures
    def underlying(self, symbols: List[str], *, time_buckets=("5m","15m","30m")) -> pd.DataFrame
        # Returns: price, volume, option_volume, flowratio, vflowratio, flownet,
        # call_volume, put_volume, put_call_ratio, dxoi, gxoi, vxoi,
        # and the time-bucket flow params

    # Aggregate GEX/DEX/VEX/CHEX
    def exposures(self, symbol: str, exps=(1, 2, 3)) -> dict
        # Returns: gex_total, dex_total, vex_total, chex_total, gex_flip,
        # gex_by_strike (dict), gex_rvol_ratio

    # Heatmap raw inputs
    def heatmap_grid(self, symbol: str, spot_pct_range=0.1, iv_shock_range=10) -> np.ndarray
        # 2D simulation surface for the GEX-VEX heatmap

    # Health
    def health(self) -> dict
        # Last-call latency, account tier, rate-limit status
```

**Why this is cleaner than the Schwab equivalent:**
- No token lifecycle (Convex auth is stable email/password)
- Vanna and Charm come from the API, not from analytical BS formulas you have to maintain
- gxoi/dxoi/vxoi pre-computed — no per-strike multiplication step
- Time-bucketed flow (5m/15m/30m) already shaped for the AM summary anomaly detector
- One library (`convexlib`) instead of `schwabdev` + auth scripts

**Internal-design note:** Define an `OptionsDataSource` Protocol that `ConvexClient` implements; if a Schwab/Barchart/Tradier client is ever added, it implements the same protocol and everything downstream keeps working.

---

## 5. The 4 core systems — implementation mapping

### 5.1 Macro theme KB (Layer 1: pgvector) — unchanged from v1

Starting batch: the 9 jdscan PDFs (~14 MB).

Pipeline (`memory/pdf_pipeline.py`):
1. Watch `data/pdfs/` (or pull from connected Google Drive)
2. SHA256-dedupe against `documents` table
3. Extract text with `pypdf` (fallback `pdfplumber` for tables)
4. Chunk ~800 tokens with 150-token overlap
5. Tag each chunk via Claude (`synthesis/tagging.py`):
   - `theme_ids`, `scope` (macro/sector/company), `sentiment` ([-1,1]), `symbols`, `date`
6. Embed with `voyage-3` (recommended) or `text-embedding-3-small`
7. Upsert into `chunks`

Query pattern: natural-language → semantic search → top-K chunks → Claude synthesis.

### 5.2 GEX/DEX/VEX/CHEX engine (simpler with Convex)

**Starting point:** `convex/advanced_gamma_scanner.py` already pulls all needed params (gamma, delta, vega, vanna, charm, oi, gxoi, dxoi, vxoi).

**Formulas (still locked in for clarity, but mostly pre-computed):**
- GEX = gxoi × spot² × 0.01 (sum across strikes; calls +, puts −)
- DEX = dxoi (sum)
- VEX (vanna) = vanna × oi × spot × IV (computable from chain when not pre-computed)
- CHEX (charm) = charm × oi × spot × 365

**Per snapshot row in `greeks_snapshots`:**
- `gex_total`, `dex_total`, `vex_total`, `chex_total`
- `gex_flip` (price where net GEX = 0; scipy.optimize.brentq)
- `atm_iv`, `iv_rank`
- `gex_rvol_ratio` (primary regime classifier — GEX normalized by 20-day realized vol)

**Snapshot cadence:** 6:45 AM + 9:45 / 12:00 / 14:00 / 15:30 during RTH + 4:30 PM close.

**Convex eliminates ~500 lines of analytical Black-Scholes code** that would otherwise sit in `greeks/black_scholes.py`. Keep a small `greeks/black_scholes.py` only for the heatmap simulation grid (which needs to shock spot/IV synthetically).

### 5.3 Earnings ripple engine — unchanged from v1

Net new build. Per ER event:
1. Identify peers (same GICS sub-industry; configurable peer-map override)
2. Window T-1 close → T+3 close
3. Classify peer reactions into 3 buckets:
   - **Direct demand** (peer same direction, high correlation to sales line)
   - **Competitive share** (peer opposite — share migration)
   - **Sentiment contagion** (peer same direction, low fundamental link; option-flow co-movement)
4. Persist to `earnings_events.peer_impacts JSONB`
5. Feed `synthesis/readthrough.py` for the AM-summary narrative

Bootstrap: SEC EDGAR for historical dates; backfill 2020–2025.

### 5.4 7 AM AM-summary generator

**Inputs assembled by `synthesis/am_summary.py`:**
1. Overnight news + earnings movers (6:30 job)
2. Greeks snapshot for watchlist (6:45 job — Convex pull)
3. VIX/VVIX/MOVE + Thrasher signal status
4. Anomaly checks (see below)
5. Macro-theme deltas from prior 24 hours
6. JD Intown scan output
7. Prior 5 days' AM summaries (continuity)

**7 anomaly checks before Claude synthesis:**
1. Spot Up + Vol Up simultaneously (vanna regime break)
2. GEX flip-point crossing today's spot
3. DEX strike migration > 2σ intraday
4. Fixed-strike IV repricing across surface
5. QOPEX rebalancing day flag
6. GEX:RVOL regime change (zone transition)
7. Thrasher VIX dispersion signal firing

**Output channels:**
- Discord webhook (primary — preserves existing workflow)
- `am_summaries` table (searchable archive)
- Streamlit Home page widget
- Email digest (optional, Phase 5+)

**Claude API:** `claude-sonnet-4-6` for daily AM summaries; `claude-opus-4-6` for weekly themes synthesis.

---

## 6. Dashboard architecture (Streamlit)

Unchanged from v1. Tab layout:

| Page | Heritage |
|---|---|
| **Home** | new — AM summary + system health |
| **1. Per-Ticker** | ABBV-style 4-panel (Price+SMA+BB+GEX overlay / GEX bars+dist / DEX bars+dist / RSI). Heritage from schwab1 |
| **2. GEX-VEX Heatmap** | new + convex's heatmap learnings |
| **3. Macro Themes** | new — pgvector search UI |
| **4. Earnings Ripple** | new |
| **5. JD Scan** | from jdscan's dashboard_v3.py |
| **6. Options Flow** | from schwab1's options_flow.py + convex's gamma_uoa_scanner.py |
| **7. Alerts Log** | from jdscan's live_alerts.py |
| **8. System Health** | new — Convex rate limits, DB lag, scheduler liveness |
| **9. ES Flow** | (optional) from convex's es_flow_viz.py |

Per-Ticker page sidebar shows latest metrics, Thrasher status, IV rank, drill-down to relevant macro chunks.

---

## 7. Scheduler architecture

APScheduler (not cron), Postgres-backed JobStore.

| Time (ET) | Job |
|---|---|
| 6:30 AM | News + earnings pull |
| 6:45 AM | Greeks snapshot for watchlist + SPX/SPY/QQQ (Convex) |
| 7:00 AM | AM summary + Discord send |
| 9:30 AM – 4:00 PM every 5 min | Live alerts |
| 9:45 / 12:00 / 14:00 / 15:30 | Intraday Greeks snapshots |
| 10:00 / 14:00 | Internals composite |
| 4:30 PM | EOD snapshot + VVIX pull |
| Sunday 21:00 | Weekly theme synthesis (Claude Opus) |

**No 7 AM Schwab token health check** — no longer relevant.

---

## 8. Secrets & credentials

### Top-level `.env`

```
# ConvexValue (PRIMARY data source)
CONVEX_EMAIL=...
CONVEX_PASSWORD=...
CONVEX_ACCOUNT_TYPE=pro

# Anthropic (Claude API for AM summary + tagging)
ANTHROPIC_API_KEY=...

# Embeddings
VOYAGE_API_KEY=...

# Macro & filings (free)
FRED_API_KEY=...

# Notification
DISCORD_WEBHOOK_URL=...    # copy from schwab1/.env

# Database
DATABASE_URL=postgresql+psycopg://intel:intel@localhost:5432/trading_intel

# Optional — kept parked
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=
BARCHART_API_KEY=
TRADIER_TOKEN=
```

`.env.template` ships with same keys, no values. `.env` in `.gitignore`. **NEVER put real credentials in `config_template.py` or any committed file again.**

### Convex credential hygiene
- **Rotate your Convex password now** — it's currently sitting in plaintext in `convex/config_template.py`
- Move to `.env` only
- For DO deployment: env vars via systemd or doctl

### Schwab token (now optional)
- Don't delete `schwab1/.env` or `token.json` — keep parked in case you reintroduce Schwab
- The daily Cowork token-health task: **disable or delete it** (your call)

---

## 9. Phased migration plan (~12 weeks)

### Phase 0 — Foundation (Week 1)
**Deliverable:** Empty `trading-intel/` repo + Docker stack (postgres + pgvector) + Alembic migrations for full schema + CI hook.

**Verify:** `docker-compose up` → connect to DB → all tables exist → `pytest` passes.

### Phase 1 — Convex client + Greeks ingestion (Weeks 2–3)
**Deliverable:** `clients/convex.py` + `greeks/exposures.py` + `greeks/flip_point.py` complete. Daily snapshot job writing to `greeks_snapshots`. Backfill of `gex_history.json` + `scheduled_gex_history.json` + `roadmap_gex_history.json` into Postgres (with `source=schwab_legacy` flag for provenance).

**Verify:** Query latest GEX flip for SPY → compare to convex/advanced_gamma_scanner.py's last output (should match within rounding).

**Go/no-go:** vanna/charm values from Convex sanity-check vs. closed-form Black-Scholes on 10 sample contracts.

### Phase 2 — Streamlit dashboard skeleton (Weeks 3–4)
**Deliverable:** Home page + Per-Ticker page (Panels 1–4) + System Health page reading from new schema. Discord alerts wired.

**Verify:** Per-Ticker page for SPY renders all 4 panels with live Convex data in <3 sec cold load.

### Phase 3 — Macro theme KB (Weeks 4–6)
**Deliverable:** `memory/pdf_pipeline.py` end-to-end. All 9 jdscan PDFs ingested. Macro Themes page semantic-search working.

**Verify:** Search "consumer stress" returns relevant chunks with proper tags. New PDF dropped into `data/pdfs/` is indexed within 5 min.

### Phase 4 — Strategy ports + JD Scan + alerts (Weeks 6–8)
**Deliverable:** `strategies/jdintown.py`, `strategies/options_flow.py`, `strategies/internals_composite.py`, `strategies/fib.py` ported. Pages 5, 6, 7 live. `live_alerts.py` job firing to Discord.

**Verify:** Yesterday's date through jdintown scan reproduces `jdintown_scan_20260425.csv` content (modulo data-feed noise).

### Phase 5 — AM summary + anomaly detection (Weeks 8–10)
**Deliverable:** `synthesis/am_summary.py` + `anomaly_detector.py` complete. Claude API integrated. 7 AM job firing daily into Discord + `am_summaries`. Thrasher signal computed with recalibrated thresholds (`scripts/recalibrate_thrasher.py`).

**Verify:** 5 days of summaries logged; each < 800 tokens; Discord embed reads cleanly.

**Go/no-go (subjective):** is the morning summary actionable on 3-of-5 days?

### Phase 6 — Earnings ripple + GEX-VEX heatmap (Weeks 10–11)
**Deliverable:** Earnings Ripple engine working on next 5 ER events. Earnings Ripple page live. GEX-VEX heatmap page live. FastAPI endpoints for top 3 views.

### Phase 7 — DO deployment + hardening (Weeks 11–12)
**Deliverable:** Droplet provisioned. Managed Postgres + app container. nginx + Let's Encrypt. Systemd scheduler. Backups. Phone-accessible status page.

**Verify:** Read AM summary on phone by 7:05 AM from anywhere. System runs 7 days unattended.

### Post-Phase 7
- Probability modeling layer (after 4–8 weeks of tagged observations)
- 5-condition confluence vol-spike model
- Knowledge-gap backfill (FWDVOL, fixed-strike vol, autocallables, dispersion, OXO2Q1)

---

## 10. Decisions still open

| # | Decision | Options | When needed |
|---|---|---|---|
| 1 | Hosting timeline | (a) Local-first 12wk → DO Phase 7. (b) DO from week 2. | Phase 0 |
| 2 | Postgres host on DO | (a) Managed ($15/mo). (b) Self-hosted on droplet. | Phase 0 |
| 3 | Embedding provider | (a) Voyage-3 (recommended). (b) OpenAI text-embedding-3-small. (c) Local sentence-transformers. | Phase 3 |
| 4 | Schwab retention | (a) **Fully retire** (recommended now). (b) Park `.env`/`token.json` for future account integration. (c) Keep daily token check running. | Phase 0 |
| 5 | Watchlist scope | (a) Mag-7 + SPX/SPY/QQQ (~10). (b) Expand ~50. (c) Dynamic (top movers + earnings calendar). | Phase 1 |
| 6 | AM summary delivery | (a) Discord only. (b) Discord + Email. (c) Discord + Email + dashboard widget. | Phase 5 |
| 7 | FastAPI in Phase 6? | (a) Yes (decouples mobile/3rd-party). (b) Streamlit-only forever. | Phase 6 |
| 8 | Google Drive auto-pull for PDFs | (a) Wire connector now. (b) Manual drop into `data/pdfs/`. | Phase 3 |
| 9 | Keep ES futures flow page | (a) Yes — port `es_flow_viz.py` as page 9. (b) Drop. | Phase 4 |

---

## 11. Risks & known weak spots

**R1 — FlashAlpha finding (HIGH).** GEX/DEX/VEX alone have no standalone edge once ATM IV is controlled for. Treat all Greek exposures as **regime descriptors**, not standalone signals, until the probability model (Phase 5+) combines them with VIX + ATM IV + credit spreads. **No alerts on raw GEX flip crossings before the probability layer exists.**

**R2 — Convex vendor dependency (MEDIUM).** Whole data layer rests on one vendor. Mitigation: build behind an `OptionsDataSource` Protocol so a Schwab/Barchart/Tradier source can drop in. Track Convex API stability + uptime in System Health page.

**R3 — Convex subscription cost (LOW-MEDIUM).** Pro tier carries a monthly cost. If subscription lapses, dashboard goes dark. Mitigation: set a calendar reminder; consider keeping Schwab `.env` warm as emergency fallback (would require ~2 days of refactoring to swap data layer back).

**R4 — 4–8 week cold start on probability layer (MEDIUM).** Backfill 2020–2025 VIX/SPX + historical earnings + synthetic theme tags from existing PDFs to bootstrap. Early AM summaries are descriptive, not predictive — set expectations.

**R5 — PDFs may contain proprietary/licensed material (MEDIUM).** The 9 jdscan PDFs are from a paid Trader community. Storing extracts in pgvector is fine for personal use; do not export/share externally. Flag `documents.source='internal'` and surface on any export.

**R6 — Convex rate limits + concurrency (MEDIUM).** Pro tier has request limits; running snapshot jobs every 5 min for ~50 tickers could throttle. Mitigation: batch requests where possible, cache with TTL, monitor in System Health.

**R7 — yfinance instability (LOW).** Fallback only; failures must degrade gracefully.

**R8 — Knowledge debt (LOW, intentional).** Five gaps (FWDVOL, fixed-strike vs floating, autocallables, dispersion, OXO2Q1) — build `docs/learning/` and treat as Phase 6+ deepening.

**R9 — Heatmap simulation cost (MEDIUM).** 2D (spot%, IV-shock) grid is expensive. Cache aggressively per-ticker, recompute only on chain refresh.

---

## 12. First three concrete actions

1. **Rotate the Convex password now** — it's in plaintext in `convex/config_template.py`. Then move credentials to `.env` only.

2. **Pick decisions 1–4 above** (or accept defaults: local-first, managed Postgres, Voyage-3, fully retire Schwab). I can default and you can override later.

3. **Decide what to do with the Schwab daily-token-health Cowork task** — delete, pause, or keep running. I recommend delete since Schwab is no longer in the daily path.

After those three, Phase 0 (scaffolding the empty `trading-intel/` repo with Docker + Alembic + pyproject.toml) can start.

---

*Document version: 2.0 — May 19, 2026 — Convex-primary architecture*
*Source projects audited: schwab1, jdscan, convex*
*Project 4 (research vision) integrated; Convex resolves 2/3 pre-build gaps*
