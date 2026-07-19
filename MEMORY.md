# MEMORY.md — Working memory for trading-intel

Living doc. Update at the end of every session. Tells future-you (and any AI assistant)
where things stand, the hard-won gotchas, and the exact commands.

---

## Current state (2026-05-27)

Institutional options-research system. **Single DB:** NAS Postgres
`192.168.1.211:5433/trading_intel` (Supabase retired). `.env` `DATABASE_URL` is shared by
the collector, dashboard, and alembic — run everything from the repo root so it's picked up.
**Alembic head: 0018.** Stack: Python 3.11, SQLAlchemy 2 + pgvector, Streamlit, ConvexValue
(primary), FRED, yfinance, Ollama (local LLM), structlog.

**Data flows from ConvexValue** through the `OptionsDataSource` Protocol (`clients/__init__.py`)
— no vendor code outside `clients/convex.py`. Greeks: `gxoi`/`dxoi`/`vxoi` are Convex-precomputed
`greek × OI` per-share; net signed GEX = `Σ sign·gxoi` (calls +, puts −); we do NOT apply
spot²/multiplier $-scaling on snapshot views (matches Convex's panels).

### Collectors (NAS DSM Task Scheduler tasks — NOT runner.py)
- Daily: `greeks_snapshot` 06:45, `chain_snapshot` 06:45 (now `chain_long`, all-expiry),
  `gex_rolling` 16:30, `oi_chain_eod` 16:35, `vol_richness` 16:40, `vix_snapshot` 16:45,
  `quotes_daily` 16:45, `am_summary` 06:55.
- Intraday RTH: `intraday_flow` 5-min, `flow_snapshot` 30-min, `delta_flow` 5-min,
  `live_gex` 10-min (now 3 Convex calls/symbol: chain + spot + flow_summary).
- Prune: `prune_intraday` hourly, `prune_oi_chain` 02:20, `prune_live_gex` 02:30.

### Dashboard pages
0 AM Report · 1 Ticker · 2 Intraday 0DTE · 3 Watchlist · 4 Flow · 5 Research Watchlist ·
6 GEX Surface (tabs: Short-term map / All-expiry map / Intraday levels) · 7 OI Flow Change ·
8 VIX · 9 Market Timing · 12 Vol Richness · 13 Delta Flow · 14 Live Gamma Map
(Live map + Forward field tabs) · 15 MM Gamma Profile · 16 Price Cone.

### Simulation tier (ADR-002 — BS recompute sanctioned for what-if views)
`greeks/black_scholes.py` (`bs_gamma`, `bs_charm`, `dollar_gamma`, `years_to_expiry`),
`greeks/flip_point.py` (zero-gamma via BS repricing + brentq), `greeks/gamma_profile.py`
(spot-ladder $Gamma per expiry, sticky-strike → page 15), `greeks/forward_field.py`
(positions/spot fixed, advance time to 16:00 → gamma/charm forward field, page 14 tab),
`prices/price_cone.py` (HAR-RV forward ±SD cone → page 16). Convex precomputed greeks remain
the default for snapshot/by-strike views; only these explicit sims recompute.

### Positioning = OI + net flow (2026-05-27)
`live_gex` stores per-(symbol,ts,strike,cp,**expiry**) greeks + OI + **volm_buy/volm_sell**.
Effective position `oi_eff = oi + (volm_buy − volm_sell)`; gamma exposure = `gxoi + gamma·net_flow`,
charm/vanna = `raw·oi_eff` (`live_gex_map_data._signed_exposure`). Falls back to OI-only when flow
absent. Per-expiry collapse is OI-weighted so `greek·oi` reconstructs cross-expiry totals.
Caveat: `volm_buy−volm_sell` doesn't separate open/close trades → flow-weighted *proxy*, not exact
net-new OI. Near-ATM only (delta band 0.30–0.70).

---

## HTML reports (template + guide)

Reusable skeleton: **`reports/_report_template.html`** (self-contained, light-mode, inline-SVG
chart engine — renders with placeholder data). How-to + checklist: **`docs/report-html-guide.md`**.
Two families, same structure/engine: Cowork **live artifact** (light mode mandatory; data via
`window.cowork.callMcpTool`; ship `create_artifact`/`update_artifact`) vs **standalone** browser
report (dark house style OK, e.g. `DVN_tas_flow_report.html`; baked-in data; ship `present_files`).
**Charts = inline SVG, NEVER Chart.js** — CDN Chart.js silently fails to paint inside Cowork
artifacts (SRI + `display:none`-at-init); this was the "all tiles empty" fix. Drive KPIs+charts from
one DATA object (static↔live differ by only the bootstrap line); verify buckets reconcile in node;
save `reports/<SYM>_<what>_<YYYY-MM-DD>.html`. Built 2026-07-16 (ORCL flow + full-analysis reports).

---

## Gotchas / learnings (read before touching infra)

**Cowork sandbox mount corruption (PERSISTENT).** The Linux mount serves
truncated/NUL-corrupted copies of just-edited files to `bash`/`cat`/`cp`/`ruff`/`pytest`
(nondeterministic; multibyte chars + recently-written files worst). **Read/Edit/Write tools
are authoritative.** Verify by reconstructing a clean tree in `/tmp` via **heredoc** (not `cp`,
which propagates the corruption) and running ruff/pytest there, plus the user's real-disk
`ruff`/`pytest`. Build minimal stub models in `/tmp` when the full `models.py` import chain truncates.

**Streamlit reloads.** Editing imported modules (`models.py`, data layers) needs a full server
**kill + relaunch** — a browser refresh / "Rerun" only re-runs the page script; `trading_intel.*`
imports stay process-cached. A lingering server PID holds old code: `netstat -ano | findstr :8501`
→ `taskkill /PID <pid> /F`. If `LiveGex has no attribute X` after a model edit → stale process.
Confirm code is live: `python -c "from trading_intel.memory.models import LiveGex; print([c.name for c in LiveGex.__table__.columns])"`.

**NAS deployment (Synology DSM).** Repo at `/var/services/homes/drmithil/trading-intel`.
- The image **bakes code** (`COPY trading_intel`); only `.env` is mounted. Source edits need an
  image rebuild **`docker build --no-cache`** (a plain build hits the `COPY` cache → identical image).
  Confirm via `Successfully tagged trading-intel:latest`.
- **Git is NOT installed on the NAS.** Update code via GitHub tarball overlay (repo is public:
  `rammpatel2013-sudo/trading-intel`), run the whole overlay+build under `sudo` (files are root-owned).
- DSM tasks (user: root) call `scripts/nas/run_job.sh <module>` — cron in `runner.py` is ignored on the NAS.
- Manual job runs need `sudo` (Docker socket is root-only). Logs at `~/ti_<job>.log`.
- `--rm` one-shot jobs fire a benign "container stopped unexpectedly" notice; `EXIT 0` is the real signal.
- `debconf: unable to initialize frontend` during build = benign (no TTY → falls back to Noninteractive).
- Migrations applied **from the laptop** over the network (`alembic upgrade head`), separate from the rebuild.
- NAS clock must be **America/New_York** (DSM fires on NAS clock; collectors stamp wall-clock ET via `timeutils.eastern_now`).

**Postgres 65535 bound-param cap.** A single `pg_insert(...).values(records)` for a wide chain
overflows it → `OperationalError`. **Batch at `_INSERT_BATCH=1000` rows** (see `oi_chain_eod`,
`chain_snapshot`). Symptom after widening a pull: `sqlalche.me/e/20/e3q8`.

**Ruff (RUF001/2/3 ambiguous unicode).** In `dashboard/pages/**` + non-test code, replace
`−`(U+2212) `×` `→` `σ` `√` `∪` `γ` with ASCII (`-`, `x`, `->`, `sigma`, `sqrt`, `+`, `gamma`).
`·`, `—`(em-dash), `Δ`, `±`, emoji are fine. Lint **only changed files** (sandbox ruff 0.15 flags
pre-existing drift). `S105` on `SCHWAB_TOKEN_PATH` is a false positive (file path). `# noqa: ANN001`
needs ANN in the select to not trip RUF100. With `from __future__ import annotations`, annotations
are unquoted (UP037). `/tmp` lint trees mis-sort imports without `known-first-party=["trading_intel"]`
— the I001 there is a harmless isolated-dir artifact.

**Charm intuition.** Instantaneous BS charm explodes ATM near expiry; the "charm → 0 at 4pm"
effect is the *remaining* charm-driven flow (`charm × time_remaining`) — the page-14 composite
weights charm by `session_fraction_remaining` (→0 at the 16:00 close) for that reason.

---

## Commands

```powershell
# >>> DEFAULT "run command" — when Mithil asks "the command" / "run my dashboard", give EXACTLY this:
cd C:\Users\drmit\PycharmProjects\trading-intel
.venv\Scripts\activate                          # prompt MUST read (.venv); (base) conda lacks deps
streamlit run trading_intel\dashboard\Home.py    # opens http://localhost:8501
# full restart after editing imported modules (model/data-layer edits stay process-cached):
#   netstat -ano | findstr :8501  ->  taskkill /PID <pid> /F  ->  re-run streamlit
# <<<

# migrations (from repo root; targets the NAS DB via .env)
alembic upgrade head
alembic current                 # expect 0018 (head)

# manual collector runs (write to the NAS DB)
python -m trading_intel.scheduler.jobs.live_gex          # 3 Convex calls/symbol; force-runs
python -m trading_intel.scheduler.jobs.chain_snapshot    # wide all-expiry pull (heavy)

# tests / lint (run locally — sandbox mount truncates)
pytest -q
ruff check trading_intel/<changed files>
```

NAS rebuild (SSH `drmithil@192.168.1.211`, then under `sudo`):
```bash
sudo sh -c '
  curl -L https://codeload.github.com/rammpatel2013-sudo/trading-intel/tar.gz/refs/heads/main -o /tmp/ti.tgz &&
  tar xzf /tmp/ti.tgz -C /tmp &&
  cp -r /tmp/trading-intel-main/. /var/services/homes/drmithil/trading-intel/ &&
  /usr/local/bin/docker build --no-cache -t trading-intel:latest /var/services/homes/drmithil/trading-intel
'
sudo bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh live_gex   # verify
tail -30 ~/ti_live_gex.log
```

Tunable `.env` knobs (no rebuild needed): `LIVE_GEX_SYMBOLS` (scope down on rate limits),
`LIVE_GEX_DELTA_LO/HI`, `CHAIN_SNAPSHOT_MAX_EXPS` (40), `CHAIN_SNAPSHOT_STRIKE_RANGE` (0.30).

---

## Pending / next

- **OI+flow live data** needs the NAS image rebuilt (collector must pull `flow_summary`); 0018
  already applied. Until rebuilt, `volm_buy/volm_sell` stay NULL → exposures are OI-only (graceful).
- All-expiry GEX surface + per-expiry maps fill **going forward** (past snapshots stay front-3).
- Optional follow-ups (offered, not built): physical "expected hedging shares" composite on page 14;
  OHLC candles / contour lines / full-chain width to match OptionsDepth; vol-richness backtest gate
  (`scripts/backtest_vol_richness.py`) before any `strategies/` promotion; AM-report top-3 vol-richness wiring.
- **VIX-decomposition history backfill** (migration 0023 family — `vix9d`, `vix3m`, `vix6m`,
  `vix_term_9d_30d`, `vix_voli_spread`, `vix_spx_beta_60d`, `vvix_vix_ratio`,
  `vix_options_richness`). Yahoo carries `^VIX9D` / `^VIX3M` / `^VIX6M` daily history; build
  `scripts/backfill_vix_term.py` to pull those + recompute the derived spreads + β + richness on
  existing `index_skew_daily` rows (whitelist-upsert per the Nations backfill pattern). Until done,
  the `TERM` and `VVOL` cards on the Vol Regime page show `unknown` severity and the classifier
  falls back to `MIXED` whenever those dimensions are needed.

### Overnight-work review + gap plan (2026-05-27, plan only — nothing built)
Inventory of what runs off-hours, across 3 scheduling surfaces:
- **NAS DSM tasks** (runner.py cron ignored on NAS): pre-market 06:45 `greeks_snapshot`/`chain_snapshot`,
  06:55 `am_summary`; post-close 16:30–16:45 `gex_rolling`/`oi_chain_eod`/`vol_richness`/`vix_snapshot`/`quotes_daily`;
  true-overnight pruners `prune_oi_chain` 02:20, `prune_live_gex` 02:30.
- **Laptop Windows Task Scheduler** (`scripts/setup_nightly_tasks.ps1`, needs local Ollama, StartWhenAvailable):
  `watchlist_ingest` 02:00 -> `research_notes` 02:15 -> `surface_reports` 02:30.
- Doc drift: runner.py says `am_summary` 07:00, MEMORY/DSM say 06:55. runner.py is dev-only.
- Verify-not-assume: runner wires `am_summary` to OllamaProvider, but it's a NAS DSM task and Ollama
  isn't on the NAS — confirm the NAS am_summary path doesn't silently fail / has a non-LLM fallback.

Gaps (planned in MASTER_PLAN / referenced in CLAUDE.md but no job exists). Decisions locked this session:
default LLM = **Ollama** for new jobs; watchdog = **log-only first** (Discord deferred). Build order by dependency:
1. **Watchdog (do first, log-only).** Create `0019_scheduled_jobs_state` + `ScheduledJobState` model +
   `scheduler/job_state.py` `record_run()` heartbeat (ON CONFLICT, rule 5) called by every job;
   `scheduler/jobs/watchdog.py` compares today's rows vs an expected-jobs manifest, emits WARNING(missing)/
   CRITICAL(errored) via structlog. NAS task ~06:30 (+ optional 03:00 for the 02:xx laptop chain).
   `clients/discord.py` (CLAUDE.md references it; doesn't exist) is a later additive swap of the log sink.
2. **Swing synthesis (#13, the open roadmap item).** `0020_swing_synthesis` + `SwingSynthesis` model;
   `synthesis/swing_synthesis.py` (reads STORED tables only — descriptors + VIX/VRP + ATM IV + IV-HV + flow +
   research_notes/surface_reports, no vendor call) + `scheduler/jobs/swing_synthesis.py` modeled on
   `surface_reports.py`. Laptop task 02:45. **Rule 4: writes `swing_synthesis`, never `signals`** — descriptive only.
3. **Weekly themes (Opus).** Build `AnthropicProvider(LLMProvider)` in `synthesis/llm.py` (only Claude-API path;
   key from .env); `0021_weekly_themes` + model; `scheduler/jobs/weekly_themes.py` aggregates the week's
   am_summaries + swing_synthesis, logs `tokens_used` (rule 7, Opus reserved here). Laptop Sun 21:00.
4. **Anomaly detector.** `synthesis/anomaly_detector.py` = 7 checks (MASTER_PLAN §5) as pure descriptor funcs,
   wired as a pre-pass into `synthesis/am_summary.py`; nudge am_summary 06:55 -> 07:00 (also fixes drift).
5. **News + earnings movers (6:30).** Needs a new vendor -> **ADR in docs/decisions/ first** (MASTER_PLAN fixes vendor set). Parked.
Missing-today building blocks confirmed: no `scheduled_jobs_state` table, no `clients/discord.py`, no `signals`
table/SignalGenerator (everything is descriptive read-through). Alembic head 0018 -> next migrations 0019+.

## Conventions / rules (see CLAUDE.md, MASTER_PLAN.md, docs/decisions/)
- **Rule 4 (FlashAlpha):** GEX/DEX/vanna/charm are regime *descriptors*, not signals. Only
  validated `strategies/` modules write `signals`.
- Migrations: additive, reversible, never edit an applied one. Idempotent collectors (ON CONFLICT).
- ADR-001 (split collector), **ADR-002** (greek recompute for simulation). Decision trail beyond
  these lives in git history.
- VEGA/VIX zones: low <22 / mid 22–32 / high >32. Default watchlist: SPX/SPY/QQQ + Mag-7 + AMD/SMCI/PLTR
  ∪ active research `watchlist_entries` (`watchlist.effective_symbols`).
