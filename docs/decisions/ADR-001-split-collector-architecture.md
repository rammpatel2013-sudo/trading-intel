# ADR-001: Split collector from heavy compute, deploy collector to DO droplet at Phase 1.5

**Date:** 2026-05-19
**Status:** Accepted
**Deciders:** Mithil

## Context

Initial MASTER_PLAN.md scheduled DO deployment for Phase 7 (week 11–12). All work — data collection, dashboard, LLM synthesis — would run on Mithil's laptop until then.

This creates a critical data-quality problem: **rolling GEX/DEX/VEX time series can only accumulate while the laptop is on.** Overnight, weekends, travel = data gaps. For:

- Thrasher VIX dispersion signal (needs continuous 20-day rolling stddev)
- GEX:RVOL regime classifier (needs unbroken series)
- Probability layer in Phase 5+ (needs 4–8 weeks of clean data to train)
- Backtest calibration of any signal

...gaps in the historical series compound into bad analysis later. By Phase 7 you'd have ~11 weeks of swiss-cheese data instead of clean continuous data.

## Decision

**Split the system into two pieces and deploy the data collector to DO at Phase 1.5 (immediately after Phase 1 data pipeline works locally).**

### What runs where

| Component | Runs on | Notes |
|---|---|---|
| Convex pulls + Greeks aggregation | DO droplet | Always-on, runs 24/7 |
| APScheduler (data jobs only) | DO droplet | Cron expression for 30-min RTH cadence |
| Postgres (Supabase) | Cloud — Supabase | Same DB both droplet and laptop write to |
| Streamlit dashboard | Laptop (Phase 2+) → migrate to DO at Phase 7 | Reads from Supabase |
| Ollama LLM (AM summary, tagging) | Laptop | Too RAM-heavy for droplet; uses laptop's 16GB |
| PDF ingestion + embeddings | Laptop | Ollama-dependent |
| AM summary generation | Laptop (Phase 5+) | Calls Ollama on laptop, writes summary to DB |
| Discord alerts | DO droplet for data-driven; laptop for LLM-driven | Both write `alerts_sent` rows |

### Collector cadence

**Every 30 minutes during US Regular Trading Hours only** (9:30–16:00 ET, Mon–Fri).

- 14 snapshots per ticker per market day (9:30, 10:00, 10:30, ..., 16:00)
- 13 watchlist tickers (SPY, QQQ, SPX, AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, SMCI, PLTR)
- ≈ 182 snapshot rows per market day
- ≈ 46,000 rows per year in `greeks_snapshots`
- Well under Supabase 500 MB free tier

Additionally:
- One end-of-day snapshot at 16:30 ET (closing GEX/DEX/VEX for daily summary)
- One pre-market check at 06:45 ET (for AM summary input)
- VIX/VVIX daily pull at 16:45 ET

### Hosting choice

DO droplet ($12/mo, 2GB RAM, 1 vCPU) — Mithil already provisioned it. Synology was an option but DO is more reliable and Mithil chose it.

No managed Postgres on DO — Supabase handles persistence. The droplet only needs the app container + APScheduler. Lean and cheap.

## Consequences

### Positive
- **Continuous data from day ~3 of the build, not day ~75.** This is a massive win for downstream analysis.
- Phase 5 probability layer arrives with 11+ weeks of clean data instead of needing post-deployment backfill.
- Splits "data integrity" (DO/Supabase) from "compute heavy" (laptop) cleanly.
- Visible win at Phase 2 dashboard: actual accumulating GEX history to display.
- Simpler ops: droplet container is single-purpose (collect data), easier to debug than full-stack deployment.

### Negative
- **$12/mo recurring cost.** Confirmed in budget.
- Two deployment targets to maintain instead of one (laptop + droplet). Mitigated: collector deploys via `docker-compose.collector.yml` and GitHub Actions auto-deploy.
- Need to monitor droplet uptime, disk usage, log rotation. Plan: weekly Discord ping from droplet "still alive" message.
- Schwab credentials parked but not used — eventually need cleanup if Schwab stays out indefinitely.

### Cost projection
- DO droplet: $12/mo (immediate, starting Phase 1.5)
- Supabase: $0 (free tier sufficient for 5+ years at this rate)
- Anthropic API: $0 (Ollama local)
- Voyage: $0 (nomic-embed-text local)
- Total: **$12/mo** for the next ~6 months of Phase 1–6 work

## Implementation plan (Phase 1.5)

After Phase 1 (`clients/convex.py` + `greeks_snapshot.py` working locally), the Phase 1.5 deliverables are:

1. Write `docker-compose.collector.yml` — minimal stack: Python container running APScheduler.
2. Write `scheduler/runner_collector.py` — composition root for the collector. Registers only the data-pull jobs, not the LLM jobs.
3. Write `Dockerfile.collector` — slim image, ~200 MB, no Ollama.
4. Provision the droplet:
   - SSH key auth, root login disabled
   - Install Docker
   - Set up systemd service for `trading-intel-collector`
   - `.env` populated with CONVEX_*, DATABASE_URL, DISCORD_*
   - **No** Ollama install, **no** Anthropic key needed
5. Push to droplet via `.github/workflows/deploy-collector.yml`.
6. Verify with `journalctl -u trading-intel-collector -f` — see "snapshot job completed" every 30 min during RTH.
7. Health check: weekly Discord ping at 09:00 ET Monday confirming the collector is alive.

Estimated effort: 1–2 working sessions after Phase 1 completes.

## Future re-evaluation

Revisit this decision at Phase 7 to decide whether to:
- Move dashboard to droplet (current plan)
- Move Ollama to droplet (would need a beefier droplet, ~$50/mo)
- Switch to Groq/Claude API for LLM at deployment (cheaper than upgrading droplet)
- Keep Ollama on laptop forever (works fine if home is always-on)

---
*Revisit triggers: budget changes, data-volume changes, vendor changes, Ollama deployment options.*
