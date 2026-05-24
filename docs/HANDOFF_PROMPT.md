# Handoff prompt — paste this at the start of the next session

> Copy everything below the line into your next Claude/AI session to resume work cleanly.
> This is the single source of truth for "where we are." Update it at the end of each session.

---

I'm continuing work on `trading-intel` — an institutional-grade stock research / options-vol system. Repo: `C:\Users\drmit\PycharmProjects\trading-intel`, GitHub `rammpatel2013-sudo/trading-intel` (private).

## Read these first, in order
1. `CLAUDE.md` — hard rules. Rule 1 (only `clients/convex.py` imports convexlib), Rule 2 (secrets only in `.env`), Rule 3 (schema via Alembic only), Rule 4 (**FlashAlpha**: GEX/DEX/VEX/CHEX are regime *descriptors*, NOT signals — only `strategies/` write to `signals`), Rule 5 (idempotent jobs), Rule 6 (pytest green before commit).
2. `MEMORY.md` — current state, formulas, Convex quirks, decision log, data-gap map.
3. This file.

## Environment / how I run things (IMPORTANT)
- **Always activate the venv first**: `.venv\Scripts\activate` — the prompt must read `(.venv)`. Plain `(base)` conda lacks deps (`structlog` etc.) and every `pytest`/`python -m ...` will fail with `ModuleNotFoundError`. This bit us twice last session.
- Test gate: `pytest -q` (in `.venv`).
- The agent sandbox **cannot reach** my DB, Ollama, or git — hand me ONE copy-paste PowerShell command at a time for anything that runs on my end. PowerShell line-continuation is backtick `` ` `` (not `^`).
- The sandbox mount intermittently serves **truncated/corrupted** copies of some files (and can't read my git index — `unknown index entry format`). Trust the canonical Windows files; verify logic inline, don't run my git from the sandbox.
- DB lives on the **NAS Postgres** (`DATABASE_URL` in `.env` points there). `alembic upgrade head` from the laptop migrates the NAS DB.
- **NAS deploy**: the NAS runs the *data collectors* as DSM Task Scheduler tasks calling a baked Docker image — code changes there need a `docker build --no-cache` + tarball (git isn't on the NAS). The nightly LLM jobs below run on the **laptop** (Ollama is local), NOT the NAS.

## Where things stand (end of 2026-05-24 session)

**Shipped + pushed to main** — research-notes bug-fix batch:
- `synthesis/watchlist_extract.py`: `_chunks()` + chunked `extract_watchlist` (unions tickers across the whole doc — fixes "only 5 tickers from a big PDF").
- `scheduler/jobs/research_notes.py`: `_ticker_excerpt()` pulls THIS ticker's section, not the doc head (fixes the wrong-ticker note). `--symbol`, `--no-llm`, progress logs. FMP dropped from the job (free tier 403s).
- `memory/watchlist_ingest.py`: `--force` re-extracts an already-ingested file (ADD-only, `ON CONFLICT DO NOTHING`).

**Built this session, ready/created locally (confirm committed — see "Open loops")** — roadmap #19 nightly surface+flow report:
- `memory/models.py` `SurfaceReport` + migration `0012_surface_reports` — **applied to NAS DB, confirmed `surface_reports: EXISTS`, alembic at `0012`**.
- `scheduler/jobs/surface_reports.py` — per `effective_symbols` ticker with an `oi_chain_eod` snapshot, generates the 3-part report (`prefer_live=False` overnight → stored flow), skips no-surface sentinels, upserts `ON CONFLICT (symbol, as_of)`. `--symbol`, `--no-llm`.
- `dashboard/surface_report_data.py` + `pages/10_Vol_Lab.py` shows the pre-computed nightly report on load (on-demand "Generate report now" button kept).
- `scripts/setup_nightly_tasks.ps1` — registers 3 **Windows Task Scheduler** jobs (laptop): `watchlist_ingest research\company` @02:00, `research_notes` @02:15, `surface_reports` @02:30. `StartWhenAvailable`. Run once from an **admin** PowerShell. Nightly ingest is deliberately NOT `--force`.

**Earlier this session (already in the tree):** Vol Lab v2 (`greeks/surface_panel.py`, `dashboard/vol_lab_data.py`, delta-indexed surface table centered at 50Δ + 6-line today/prior chart, `surface_changes.delta_change_profile`); IV-HV screener + Charts page (`prices/technicals.py` Wilder RSI, `dashboard/chart_data.py`, `pages/11_Charts.py`); gamma-regime classifier (`greeks/gamma_regime.py`, `dashboard/gamma_regime_data.py`); market-timing page (`pages/9_Market_Timing.py`); VIX term-structure + VRP + decomposition (migration `0010`, `pages/8_VIX.py`); `clients/edgar.py` (SEC 10-K, keyless, User-Agent), `clients/fmp.py` (stable API — but free tier 403s, so unused by jobs); on-demand live pull (`dashboard/live_refresh.py`); guides in `docs/guides/`.

### Decisions locked (don't re-litigate)
- **Convex-only** for options. FMP free tier 403s → not used by jobs (kept as a client). Reddit dropped. No IBKR (always-on cost).
- Nightly LLM jobs run on the **laptop** via Task Scheduler (local Ollama `qwen2.5:3b`, CPU, slow — overnight is fine). Don't bump to a 14b model (no RAM).
- GEX units = raw net signed gxoi (calls +, puts −), no multiplier. Flip = BS repricing.

## This session — priorities

1. **#13 — Swing-trade synthesis** (the one open roadmap item). Combine the regime descriptors + VIX + ATM IV + IV-HV + flow + research notes into a descriptive "what's setting up" read. **FlashAlpha rule 4**: this is a read-through, NOT a `signals`-table write unless it goes through a validated `strategies/` scanner or the Phase-5 probability model.

## Open loops to confirm at the start of next session
- Did the #19 batch get **committed**? (`git add models.py 0012_surface_reports.py surface_reports.py surface_report_data.py 10_Vol_Lab.py setup_nightly_tasks.ps1` → commit). Check `git status --short` (should be clean) and `git log origin/main..HEAD` (should be empty).
- Did `scripts\setup_nightly_tasks.ps1` get run (admin)? Verify: `Get-ScheduledTask -TaskName 'TradingIntel-*'`.
- After the first overnight run, sanity-check the table populated: `SELECT symbol, as_of, flow_source FROM surface_reports;` (empty for a symbol just means no `oi_chain_eod` snapshot for it yet — skipped by design).
- Optional, when you want all tickers from the big "hidden angles" PDF: `python -m trading_intel.memory.watchlist_ingest research\company --force` then `python -m trading_intel.scheduler.jobs.research_notes --symbol <SYM>`.

## Rules of engagement
- Show the plan before writing code; get approval, then build.
- Activate `.venv` before any python/pytest/alembic command.
- pytest green before any commit; hand me one copy-paste command at a time.
- `deploy.yml` stays manual-only; CI (`ci.yml`) runs on push.

End of handoff.
