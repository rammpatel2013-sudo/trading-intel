# Handoff prompt — paste this at the start of the next session

> Copy everything below the line into your next Claude/AI session to resume work cleanly.

---

I'm continuing work on `trading-intel` — an institutional-grade stock research intelligence system. Repo lives at `C:\Users\drmit\PycharmProjects\trading-intel` and on GitHub at `rammpatel2013-sudo/trading-intel` (private).

## Read these docs first, in order

1. `CLAUDE.md` — hard architectural rules. Especially Rule 1 (data-source isolation: only `clients/convex.py` imports convexlib), Rule 2 (secrets only in `.env`), Rule 3 (schema via Alembic only), Rule 4 (FlashAlpha: Greeks are regime descriptors, NOT signals — only `strategies/` write to `signals`), Rule 5 (idempotent jobs).
2. `MEMORY.md` — current state, formulas, Convex quirks, decision log, and the **Data-gap analysis** section (2026-05-21). Update it at the end of the session.
3. `MASTER_PLAN.md` + `LONG_TERM_PLAN.md`.

## Where things stand (pushed to main @ 72bdba7, 2026-05-21)

**Phase 1 day 2 — Greeks ingestion (DONE):** `greeks_snapshot.py` (06:45 ET near-term GEX/DEX/VEX/CHEX + flip + ATM IV → `greeks_snapshots`) and `gex_rolling.py` (16:30 ET 6-month rolling GEX → `gex_rolling` + `gex_term`). Validated vs the ConvexValue app (SPY front-month net matched within ~2%).

**Phase 1.5b — Research knowledge pipeline (DONE):** `memory/pdf_pipeline.py` + `synthesis/tagging.py` + `synthesis/prompts.py`. Walks `research/`, extracts PDF/docx, writes framework notes → `docs/playbooks/*.md` (gitignored, local-only) and theme tags → Supabase (`themes`/`theme_observations`), via local Ollama (`qwen2.5:3b`). Ingested 13/13 research docs. 25 tests green, lint clean.

### Decisions locked (don't re-litigate)
- **GEX units = raw net signed gxoi** (calls +, puts -), matching the ConvexValue app. NO multiplier, NO dollar-scaling. VEX=`Σ vanna·oi·spot·iv`, CHEX=`Σ charm·oi·spot·365`, DEX=`Σ dxoi`. Flip = BS repricing, brentq over ±10%.
- **Two knowledge types** (`documents.kind`, migration 0003): **methodology** (knowledge FOR the LLM — frameworks applied to live data to find/interpret trades; this is what we ingested so far) vs **research** (knowledge ABOUT companies/themes — deep research, watchlists, Q&A; not built yet).
- Convex quirks (no `cxoi`; `get_chain_as_rows` row layout; `get_und` rows at `data[0]`; epoch-day expirations) — all handled in `clients/convex.py`.

## This session — pick one (see MEMORY "Data-gap analysis" for the full map)

The research playbooks make the required data model explicit. Four tables exist but **no job writes them yet** — that's the leverage. Priority order:

1. **Per-strike `greeks_chain` collector (highest leverage).** Convex's chain already returns per-strike IV/greeks/gxoi; we currently aggregate and discard the rows. Persisting them over time unlocks the implied-vol surface, smile/skew, SABR calibration, cumulative-gamma-by-strike, max pain, and vol-of-vol. Pure Convex, table ready, FlashAlpha-safe (data-only).
2. **`quotes_daily` OHLCV + realized vol (rv20/rv60).** Needed for IVAR (implied-vs-realized) and the GEX:RVOL classifier and the dr.wish MA/stochastic rules. Source: yfinance fallback or Convex und history.
3. **`vix_data`** — VIX/MOVE/credit via FRED (key present); VVIX + VIX term structure (VXST/VIX/VXV/VXMT) via a CBOE scrape (`clients/cboe.py`, not built). Feeds the FlashAlpha probability model.
4. **`flow_buckets`** — flowratio/vflowratio + 5m/15m/30m bucketed flow (the convex.docx 4-condition framework). Add bucketed-flow params to the chain pull.

**Alternative tracks (also logged in MEMORY):** the **Type-2 company-research layer** (embeddings into `chunks` pgvector + symbol-keyed Q&A + watchlists — the "knowledge about companies" half), the **24/7 collector** to the DO droplet (ADR-001 — its `runner_collector` should register the new data jobs above), a **Convex-style dashboard view** (joy-plot / gxoi-by-expiration), or the **AM summary**.

Before coding a Convex collector: **verify the exact field names against the convexlib field list** (bucketed-flow params, `volga`, VIX/VVIX) — one bad param 400s the whole chain request.

## Rules
- Show the plan before writing code; get approval, then build.
- Run on my machine via `.venv\Scripts\python -m ...` (base conda lacks deps; e.g. `.venv\Scripts\alembic upgrade head`).
- pytest green before any commit (`.venv\Scripts\python -m pytest -q`).
- Hand me ONE copy-paste command at a time when something runs on my end (Ollama, Supabase, git all run from my Windows terminal — the agent sandbox can't reach them).
- `deploy.yml` is gated to manual-only; don't un-gate it until the prod stack exists. CI (`ci.yml`) runs on push.
- Note: editing existing CRLF files with the Edit tool has corrupted files before — prefer full rewrites or shell edits for existing files.

End of handoff.
