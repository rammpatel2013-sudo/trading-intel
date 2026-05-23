# Next session — daily AM report (research-watchlist aware) + dashboard view

## Goal
Generate a **daily AM report** that summarizes the current regime across the
**effective watchlist** — static symbols PLUS the tickers surfaced from company
research — and surface it **on the dashboard**. Two halves to the goal Mithil
stated: (1) when research is ingested and the watchlist updates, the next AM
report should automatically cover those new tickers with their research
rationale; (2) he wants to *see how it looks* — a dashboard page rendering the
report, not just a Discord push.

The `am_summaries` table already exists (no migration needed). There is **no
generator or job yet** — that's this phase.

## Context (read first)
- Repo: `C:\Users\drmit\PycharmProjects\trading-intel`. Read `CLAUDE.md` (esp. rule 7 cost-aware LLM, rule 4 FlashAlpha) and `MEMORY.md` (`### NAS deployment`, Schedule, Formulas).
- Live data in NAS Postgres (`postgresql+psycopg://intel:intel@192.168.1.211:5433/trading_intel`, head `0008`): `greeks_snapshots` (gex/dex/vex/chex_total, spot, gex_flip, atm_iv), `greeks_chain`, `gex_rolling`/`gex_term`, `quotes_daily` (+ rv20/rv60), `flow_snapshots`, `intraday_flow` (SPX/SPY/QQQ), `watchlist_entries` (research tickers + rationale/sentiment/source_doc_id).
- **Storage target:** `AmSummary` in `memory/models.py` — columns `date` (PK), `markdown` (Text), `metadata_json`, `claude_model`, `tokens_used`. One row per day, markdown body.
- Reuse — do NOT duplicate compute:
  - `trading_intel/watchlist.py` `effective_symbols(session, settings)` — the symbol universe (static ∪ active research).
  - `dashboard/watchlist_metrics.py` `load_watchlist_metrics` / `build_watchlist_row` — per-ticker GEX + dir, weekly ΔGEX, C/P OI, vol/OI, skew, walls + CW distance, gamma regime/concentration.
  - `dashboard/flow_data.py` (largest prints / P-C tilt / net premium), `dashboard/dynamic_watchlist.py` (research entries + rationale/sentiment), `dashboard/ticker_data.py` intraday readers (0DTE volume-weighted gamma/vanna/charm).
  - `synthesis/llm.py` `LLMProvider` / `OllamaProvider` (`.chat(messages, model=, max_tokens=)`); add the prompt to `synthesis/prompts.py`. Daily LLM = **Ollama local** (free) per the project stack — use `settings.LLM_DAILY_MODEL`, NOT the Anthropic API, unless Mithil asks. (`claude_model`/`tokens_used` columns can stay null or record the local model name.)
  - Delivery pattern: `clients/discord.py` (07:00 AM summary + Discord is in the Schedule).
  - Existing report-shaping reference: `synthesis/surface_report.py`.

## Task
1. **Pure context builder** — `synthesis/am_summary.py`:
   - `build_am_context(session, settings) -> AmContext` (dataclass/pydantic): pulls the effective watchlist, per-ticker regime metrics, flow highlights, SPX/SPY/QQQ 0DTE intraday read, week-over-week ΔGEX, and the **research-surfaced tickers with their rationale/sentiment** (flag which symbols are research-driven vs static). All descriptive — FlashAlpha rule 4, no signals.
   - `render_am_markdown(ctx, llm: LLMProvider, settings) -> tuple[str, dict]` — builds the prompt (in `prompts.py`), calls the LLM, returns `(markdown, metadata)`. Keep a deterministic non-LLM fallback (tabular markdown from the context) so the report still generates if Ollama is down.
   - Lead the report with a market-wide regime line (SPX/SPY/QQQ), then a research-watchlist section (new tickers + why they're on the list), then the rest of the watchlist.
2. **Job** — `scheduler/jobs/am_summary.py` with `run(session, source, llm, *, settings)` + `main()` entrypoint (mirror existing jobs). Idempotent upsert into `am_summaries` (`ON CONFLICT (date) DO UPDATE` so a re-run refreshes today). Optional Discord send guarded by a settings flag. Register in `scheduler/runner.py` at ~07:00 ET (dev/runner only — NAS uses a DSM task, see below).
3. **Dashboard page** — `dashboard/pages/0_AM_Report.py` (leading `0_` so it sorts to the top): date selector (default latest), render `am_summaries.markdown` with `st.markdown`, show metadata (model, generated-at). Thin page; any data prep → pure helper in `dashboard/am_report_data.py` (reader: latest + by-date).
4. **Tests** — `tests/synthesis/test_am_summary.py` (context builder against in-memory SQLite seeded rows; `render_am_markdown` with a stub `LLMProvider`), `tests/scheduler/test_am_summary.py` (idempotent upsert), `tests/dashboard/test_am_report_data.py`.

## Constraints / gotchas
- **File-edit tools truncate large files** — write/modify via shell heredoc + `python -c "import ast; ast.parse(...)"` after each change.
- ruff `select = E,F,I,N,W,B,UP,ANN,S,RUF`, line-length 100, tests ignore ANN/S. **Lint only changed files** — sandbox ruff is newer than the pinned one and flags ~32 pre-existing issues that are NOT yours (see MEMORY dev-workflow gotchas).
- Sandbox Python 3.10 vs repo 3.11: shim `datetime.UTC = datetime.timezone.utc` before pytest.
- Streamlit pages aren't headless-testable — factor pure helpers, keep page thin.
- pytest green + ruff-clean-on-changed-files before done.
- **Mithil runs git + all live/NAS infra.** Hand him ONE copy-paste command at a time. He can't `git push` from here — you commit, he pushes (PyCharm).
- FlashAlpha rule 4: regime descriptors only, no signals/predictions (incl. for research tickers).

## Deploy (after merge — Mithil, on the NAS; see MEMORY `### NAS deployment`)
- Update image: tarball overlay + `docker build --no-cache -t trading-intel ./trading-intel` (git isn't installed on the NAS).
- Add a DSM Task Scheduler task `trading-intel am-report`: same docker wrapper, `... trading-intel python -m trading_intel.scheduler.jobs.am_summary`, **Daily ~06:55** (NAS clock = market TZ). Runner.py cron is ignored on the NAS.

## Verify
- Locally (DATABASE_URL → NAS): `python -m trading_intel.scheduler.jobs.am_summary` writes today's row; open the AM Report page and confirm it renders, including a research-watchlist section. Drop a research file → `python scripts/sync_research_watchlist.py` → re-run the job → confirm the new ticker appears in the report with its rationale.
