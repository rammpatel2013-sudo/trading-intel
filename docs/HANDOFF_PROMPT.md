# Handoff prompt — paste this at the start of the next session

> Copy everything below the line into your next Claude/AI session to resume work cleanly. It tells the model exactly where we are and what to build.

---

I'm continuing work on `trading-intel` — an institutional-grade stock research intelligence system. Repo lives at `C:\Users\drmit\PycharmProjects\trading-intel` and on GitHub at `rammpatel2013-sudo/trading-intel` (private).

## Read these three docs in order before doing anything

1. `C:\Users\drmit\PycharmProjects\trading-intel\CLAUDE.md` — hard architectural rules. **Do not violate them.** Particularly Rule 1 (data source isolation), Rule 2 (secrets only in `.env`), and Rule 4 (the FlashAlpha rule: Greek exposures are regime descriptors, NOT signals).
2. `C:\Users\drmit\PycharmProjects\trading-intel\MEMORY.md` — current state, what's done, what's next, open decisions, recent decision log. Update this at the end of every session.
3. `C:\Users\drmit\PycharmProjects\trading-intel\MASTER_PLAN.md` — the 12-week tactical plan and full architecture. `LONG_TERM_PLAN.md` is the 1–3 year strategic view.

## Where things stand (Phase 1 day 1 done)

End-to-end stack is wired and verified:
- **Supabase** (project `wrjizvhwsotoeymyjrcu`) — PostgreSQL + pgvector with 14 tables already created via Alembic migration `0001_initial_schema.py`. DATABASE_URL is in `.env`.
- **Ollama** running locally at `http://localhost:11434` with `qwen2.5:3b` (LLM) and `nomic-embed-text` (embeddings) pulled. 16 GB RAM — keep the 3b model, NOT 14b.
- **ConvexValue Pro** API verified working. Smoke test `api.get_und(['SPY'])` returned `[['SPY', 734.29]]`. Credentials in `.env`. SDK installed via `pip install git+https://github.com/convexvalue/convexlib.git`.
- **Discord** — 7 webhooks (general, flow, IV, VEX, signals, internals, trends) in `.env`.
- **Python venv** at `.venv\` with all deps installed via `pip install -e ".[dev]"`.

## Today's specific deliverable — Phase 1 day 2

Build the first piece of real data ingestion. Concretely:

1. **Replace the skeleton** `trading_intel/clients/convex.py` with a working `ConvexClient` class implementing the `OptionsDataSource` Protocol defined in `trading_intel/clients/__init__.py`. Methods required: `chain(symbol, exps, strike_range)`, `underlying(symbols, time_buckets)`, `exposures(symbol, exps)`, `health()`. Wraps `convexlib.api.ConvexApi`. Returns pandas DataFrames with normalized column names. **The ONLY file that imports `convexlib` is this one.**

2. **Write `trading_intel/greeks/exposures.py`** — compute GEX/DEX/VEX/CHEX from a normalized chain DataFrame. Locked formulas (DO NOT modify):
   - GEX = `(gxoi * sign).sum()` where sign is +1 for calls, -1 for puts, on Convex's `gxoi` column (which equals `gamma × oi × multiplier`). Multiply by `spot² × 0.01` only if the column isn't already in dollar terms — check Convex docs / inspect a sample.
   - DEX = `dxoi.sum()`
   - VEX = `(vanna * oi * spot * iv).sum()` (compute since Convex's `vxoi` is vega-based, not vanna)
   - CHEX = `(charm * oi * spot * 365).sum()`

3. **Write `trading_intel/greeks/flip_point.py`** — find price where net GEX crosses zero using `scipy.optimize.brentq` over a 10% range around current spot.

4. **Write `trading_intel/scheduler/jobs/greeks_snapshot.py`** — single function `run(session: Session, source: OptionsDataSource) -> None` that:
   - Iterates the watchlist from `Settings().watchlist_symbols`
   - For each ticker calls `source.exposures(symbol)`
   - Builds a `GreeksSnapshot` ORM row and inserts with `INSERT ... ON CONFLICT (symbol, ts, source) DO NOTHING`
   - Logs via `structlog` with `correlation_id`

5. **Write a test** `tests/clients/test_convex.py` that mocks `convexlib.api.ConvexApi` and verifies `ConvexClient.exposures()` returns the expected dict shape. No real API calls in tests.

6. **Manually trigger the job once**: `python -m trading_intel.scheduler.jobs.greeks_snapshot`. Check Supabase Table Editor → `greeks_snapshots` should have 13 new rows (one per watchlist ticker).

7. **Commit and push.**

## Rules I must follow this session

- **No `convexlib` imports outside `clients/convex.py`.** Downstream code consumes data via the `OptionsDataSource` Protocol.
- **No alerts on raw GEX flip crossings** — FlashAlpha rule. Greeks are regime descriptors. Strategy modules under `strategies/` are the only things that emit signals.
- **No secrets in code.** Everything from `.env` via `Settings` (pydantic-settings).
- **All schema changes through Alembic.** No `CREATE TABLE` in app code. Schema for today already exists — no new migrations needed yet.
- **Idempotent writes.** `INSERT ... ON CONFLICT DO NOTHING` on snapshot rows.
- **Python 3.11+, type hints everywhere, modules under 400 lines.**

## Things I do NOT need this session

- Streamlit dashboard work (Phase 2)
- PDF ingestion (Phase 3)
- AM summary / Claude synthesis (Phase 5)
- DO deployment (Phase 7)

Start by reading the three docs listed above. Then propose the specific code for `clients/convex.py` and we'll iterate. Don't write code blindly — show me your plan first, get approval, then write.

End of handoff.
