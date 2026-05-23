# trading-intel

Institutional-grade stock research intelligence system. Cross-theme analysis tying macro signals to company outcomes, GEX/DEX/VEX/CHEX volatility surfaces, earnings ripple detection, and a daily 7 AM AM-summary brief.

**Status:** Phase 0 (foundation). See `MASTER_PLAN.md` for the full roadmap.

## Quick start (local dev)

```bash
# 1. Clone and enter
git clone git@github.com:YOUR_USER/trading-intel.git
cd trading-intel

# 2. Copy env template and fill in real values
cp .env.template .env
# edit .env — at minimum: CONVEX_EMAIL, CONVEX_PASSWORD, ANTHROPIC_API_KEY

# 3. Start the database
docker compose up -d postgres

# 4. Install deps and run migrations
pip install -e ".[dev]"
alembic upgrade head

# 5. Run the dashboard
streamlit run trading_intel/dashboard/Home.py

# 6. Run the scheduler (in a second terminal)
python -m trading_intel.scheduler.runner
```

Dashboard at `http://localhost:8501`. Scheduler runs APScheduler jobs (see `scheduler/jobs/`).

## Core docs in this repo

| File | Purpose |
|---|---|
| `MASTER_PLAN.md` | Full 12-week build plan, 7 phases |
| `CLAUDE.md` | Rules for AI-assisted development. **Read this before any AI agent touches the code.** |
| `MEMORY.md` | Working memory: current state, recent decisions, open questions |
| `DEPLOYMENT.md` | Local → GitHub → Digital Ocean deployment guide |
| `docs/decisions/` | Architecture Decision Records (one per non-obvious choice) |
| `docs/frameworks/` | Reference notes: Thrasher, FlashAlpha, nextSignals |
| `docs/playbooks/` | Trading playbooks ingested from PDFs |

## Architecture (one-page summary)

- **Data source:** ConvexValue pro tier (pre-computed Greeks, time-bucketed flow). Behind an `OptionsDataSource` Protocol so Schwab/Barchart/Tradier can drop in if Convex ever fails.
- **DB:** PostgreSQL 16 + pgvector. Single instance handles structured (L2) and vector (L1) data.
- **L3 memory:** JSON snapshots in `data/snapshots/` (mirror to Google Drive optional).
- **Synthesis:** Claude API (sonnet-4-6 daily, opus-4-6 weekly).
- **Embeddings:** Voyage-3.
- **UI:** Streamlit (8–9 tabs). FastAPI in Phase 6+ for mobile/3rd-party.
- **Schedule:** APScheduler with Postgres-backed JobStore.
- **Hosting:** Local-first → Digital Ocean droplet ($24/mo) at Phase 7.

## Architectural rules (non-negotiable)

1. **No standalone Greek-exposure signals.** Per the FlashAlpha backtest finding, GEX/DEX/VEX have no edge once ATM IV is controlled for. Treat them as regime descriptors, not signals, until the probability model exists (Phase 5+).
2. **All external data behind a Protocol.** No direct `convexlib` or `schwabdev` calls outside `clients/`. Downstream code depends on the Protocol, not the vendor.
3. **No secrets in code.** Only in `.env` (gitignored). `.env.template` is checked in with empty values.
4. **All schemas live in Alembic.** Never `CREATE TABLE` outside a migration.
5. **One `OptionsDataSource` per process.** Inject via dependency, don't instantiate ad-hoc.

## License

Personal use. PDFs in `data/pdfs/` may contain proprietary material — do not redistribute.
