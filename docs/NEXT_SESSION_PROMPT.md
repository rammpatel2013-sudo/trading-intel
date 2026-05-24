# Start here — next trading-intel session

Two parts: (A) unstick the methodology ingest **now**, then (B) the paste-ready
prompt + commands for the next session.

---

## A. Unstick the ingest (do this now)

The run stalled around document_id 3–10 (~8 docs). It's safe to interrupt —
ingest commits per-doc and dedupes by content hash, so nothing is lost and a
re-run resumes. The stall is one specific doc (the next un-ingested file in
sorted order — likely a large/scanned PDF grinding on the `pdfplumber` fallback,
or Ollama thrashing on too big a model).

```powershell
# 1. Ctrl-C the stuck run, then confirm Ollama isn't thrashing on a big model:
ollama ps
#    If it shows a 14b/32b model, set LLM_DAILY_MODEL=qwen2.5:3b in .env first.

# 2. List the sorted order; the culprit is the file right after the ~8 done:
.venv\Scripts\python -c "from pathlib import Path; from trading_intel.memory.pdf_pipeline import discover_documents; [print(i, p.name) for i, p in enumerate(discover_documents(Path('research/doc')))]"

# 3. Move that one file aside for now:
#    mkdir research\doc\_skip
#    move "research\doc\<stuck-file>.pdf" "research\doc\_skip\"

# 4. Run the NEW sync command. It backfills embeddings for the ~8 already-done
#    docs AND ingests+embeds the remaining ones, idempotently (skips the moved file):
.venv\Scripts\python -m trading_intel.memory.sync_knowledge --skip-research
```

Why `sync_knowledge` and not `pdf_pipeline` again: your first run used the OLD
code (no embeddings). Re-running `pdf_pipeline` would skip the done docs and
never embed them. `sync_knowledge` detects docs with zero chunks and backfills
their embeddings — no LLM regeneration — while ingesting the rest. The
moved-aside file is probably image-heavy/scanned (needs OCR, out of scope);
decide later whether to keep it.

---

## B. Paste this as the prompt to start next session

> Continuing trading-intel (C:\Users\drmit\PycharmProjects\trading-intel, PowerShell; I run all git/NAS/Ollama). Read in order: CLAUDE.md, MEMORY.md (esp. the newest 2026-05-23 session entry: methodology RAG substrate + sync_knowledge + ΔIV positioning + fixed-strike heatmap + VIX), and docs/NEXT_SESSION.md.
>
> State: Last session shipped + unit-tested (canonical files authoritative; the cowork mount was truncating — verify via Read, lint/test against heredoc-rebuilt /tmp copies). Built: item 2 (chunking/embeddings/retrieval/sync_knowledge with supersede+prune+backfill, surface_report KB upgraded to semantic retrieval); item 1 (per-strike ΔIV + positioning label in oi_changes, on page 7); Track 2 (fixed-strike ΔIV strike×expiry heatmap); Track 3 (clients/fred.py + clients/cboe.py + vix_snapshot job + vix_view + page 8, registered 16:45). No migrations. 22 methodology PDFs ingested + embeddings backfilled via sync_knowledge.
>
> This session, in priority order:
> 1. Wire the methodology RAG into the AM report: in synthesis/am_summary.render_am_markdown, build a query from the day's regime, call memory.retrieval.retrieve_chunks(kind="methodology"), inject format_kb(hits) into AM_SUMMARY_PROMPT as desk grounding (mirror surface_report). Keep the Ollama-down fallback; mock retrieval in tests. This is the deferred item-2 finish.
> 2. Verify the data-gated pages now that the Tue 5/26 EOD has landed: run scripts/verify_oi_flow.py (≥2 oi_chain_eod snapshots, native oi_ch vs our ΔOI sign-agreement, ΔGEX/mean-ΔIV, GEX surface 2nd column); eyeball page 7 (ΔIV + positioning) and the GEX surface. Confirm chain_snapshot/greeks_snapshot are actually scheduled on the NAS (they had NO DSM task).
> 3. Verify the CBOE endpoints in clients/cboe.py (built blind last session): hit cdn.cboe.com _VVIX/_VIX9D/_VIX/_VIX3M/_VIX6M, confirm the JSON key, fix _BASE/_parse_price if needed, then run vix_snapshot and open page 8.
> 4. Handle the one scanned/image-heavy PDF I set aside in research/doc/_skip (decide OCR vs drop).
>
> Gotchas (carry-over): cowork mount serves STALE/TRUNCATED/NUL copies even of canonical files via bash — Read/Edit/Write tools are authoritative; rebuild clean /tmp copies via heredoc (not cp) for lint/test. Sandbox Py3.10 → shim datetime.UTC=timezone.utc; pytest --assert=plain -p no:cacheprovider. SQLite tests: per-table create only (chunks has pgvector+ARRAY); vector SQL runs only on real Postgres → mock it. Lint only changed files. FlashAlpha rule 4: descriptive only. Show me the plan before coding.

---

## C. Commands for next session (PowerShell)

```powershell
# After the Tue 5/26 EOD close — verify the OI study + GEX surface:
.venv\Scripts\python scripts\verify_oi_flow.py

# Verify CBOE shape, then snapshot once and view the dashboards:
.venv\Scripts\python -m trading_intel.scheduler.jobs.vix_snapshot
.venv\Scripts\streamlit run trading_intel\dashboard\Home.py   # VIX + OI & Flow Change pages

# Re-scan knowledge anytime (add --prune-removed to honor deletions):
.venv\Scripts\python -m trading_intel.memory.sync_knowledge
```

NAS / git hand-off (after you push `main`): add DSM tasks for `am_summary`
(~06:55) and `vix_snapshot` (~16:45), and re-deploy `oi_chain_eod` with the
batch fix. Full steps are in MEMORY.md "### NAS deployment".
