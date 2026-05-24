# Next session — wire the methodology RAG into the AM report, verify the data-gated pages, ship VIX live

## State coming in (end of 2026-05-23 session)
Built + tested this session (canonical Windows files authoritative; sandbox mount
was badly truncating — see MEMORY "Sandbox gotcha"):
- **Methodology RAG substrate** (item 2): `memory/chunking.py`, `memory/embeddings.py`,
  embed-on-ingest in `pdf_pipeline`, `memory/retrieval.py`, `memory/sync_knowledge.py`
  (auto-scan + supersede + prune + embedding backfill), and `surface_report.load_kb_context`
  upgraded to semantic retrieval (file-concat fallback). No migration (chunks table existed).
- **ΔIV positioning analytic** (item 1) in `dashboard/oi_changes.py` + surfaced on page 7.
- **Fixed-strike ΔIV heatmap** (Track 2) — `changes.fixed_strike_change_matrix` + Ticker panel.
- **VIX dashboard** (Track 3) — `clients/fred.py`, `clients/cboe.py`, `scheduler/jobs/vix_snapshot.py`,
  `dashboard/vix_view.py`, `pages/8_VIX.py`, registered in `runner.py` (16:45 ET).
- `scripts/verify_oi_flow.py` (read-only post-EOD checker).

All unit-tested on SQLite/mocks + ruff-clean on changed files.

## Priorities, in order

1. **Wire the methodology RAG into the AM report** (the deferred item-2 finish).
   `synthesis/am_summary.render_am_markdown` currently feeds only the deterministic
   tables to `AM_SUMMARY_PROMPT`. Add a retrieval step: build a query from the day's
   regime (gamma regime, skew/IV, flow tilt), call `memory.retrieval.retrieve_chunks(
   session, llm, query, kind="methodology")`, and inject `format_kb(hits)` into the
   prompt as desk-methodology grounding (mirror how `surface_report` now does it).
   Keep the Ollama-down fallback. Pure context-build should stay testable; mock retrieval.

2. **Verify the data-gated pages once Tue 2026-05-26 EOD lands.**
   Run `python scripts/verify_oi_flow.py` — confirms ≥2 `oi_chain_eod` snapshots, that
   Convex's native `oi_ch` sign-agrees with our ΔOI, the ΔGEX/mean-ΔIV roll-up, and that
   the GEX surface gained a 2nd daily column. Then eyeball page 7 (ΔIV + positioning
   columns) and the GEX surface page. Also confirm `chain_snapshot`/`greeks_snapshot` are
   actually scheduled on the NAS (they had NO DSM task — that's why these accumulate slowly).

3. **Verify the CBOE endpoints in `clients/cboe.py`** (built blind this session).
   Hit `https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VVIX.json` (and `_VIX9D`,
   `_VIX`, `_VIX3M`, `_VIX6M`) once live, confirm the JSON key for the level, and fix
   `_BASE` / `_parse_price` if CBOE has changed the shape. Then run `vix_snapshot` once and
   open page 8.

4. **Backfill methodology embeddings** if not already done: after the 22-PDF ingest,
   `python -m trading_intel.memory.sync_knowledge --skip-research`.

## Hand-off commands (Mithil, PowerShell — one at a time)
- Finish ingest then embed: `.venv\Scripts\python -m trading_intel.memory.sync_knowledge --skip-research`
- Full re-scan both folders (going forward): `.venv\Scripts\python -m trading_intel.memory.sync_knowledge`
  (add `--prune-removed` to honor deletions)
- After Tue 5/26 EOD: `.venv\Scripts\python scripts\verify_oi_flow.py`
- NAS (after `git push`): add DSM tasks for `am_summary` (~06:55), `vix_snapshot` (~16:45),
  re-deploy `oi_chain_eod` with the batch fix; rebuild image `--no-cache` (see MEMORY NAS deployment).
- Optional laptop nightly auto-scan (needs Ollama): Windows Task Scheduler →
  `.venv\Scripts\python -m trading_intel.memory.sync_knowledge`.

## Constraints / gotchas (carry-over — see MEMORY for detail)
- **Cowork mount serves STALE/TRUNCATED/NUL copies** of even canonical files via bash;
  Read/Edit/Write tools are authoritative. Reconstruct clean `/tmp` copies via **heredoc**
  (not `cp`) for lint/test. Sandbox is Py3.10 → shim `datetime.UTC = timezone.utc`; run
  `pytest --assert=plain -p no:cacheprovider`. Lint only changed files (sandbox ruff stricter).
- SQLite tests: create only the tables you need (never `Base.metadata.create_all` — `chunks`
  has pgvector + ARRAY cols). Embeddings/vector SQL run only on real Postgres; unit-test the
  pure pieces + mock the vector search.
- FlashAlpha rule 4: regime descriptors only, no signals.
- Mithil runs all git/NAS/Ollama; sandbox can't reach GitHub/NAS-LAN/Ollama.
