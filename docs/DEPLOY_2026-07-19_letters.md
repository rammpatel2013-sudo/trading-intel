# Investor-letters pipeline — spine build + deploy (2026-07-19)

*Built the full spine (Substack digests + EDGAR 13F holdings) per
`docs/investor_letters_pipeline.md`. Source list: `Investor_Letters_Tracker.xlsx`.
Pure cores unit-tested by direct execution; the DB/Ollama/EDGAR runtime is handed off.*

## What shipped

**Reuse-first** — letters are just `Document`s, so tickers flow through the EXISTING
`memory.watchlist_ingest.ingest_folder` -> `watchlist_entries` (the RESEARCH watchlist)
and the knowledge/chunk pipeline. New code is only the fetchers + the 13F diff.

- `trading_intel/letters/sources.py` — source registry (6 Substack feeds, 16 EDGAR 13F
  CIKs), de-duplicated. Extend from the tracker's `SEC CIK` column.
- `trading_intel/letters/substack.py` — `parse_feed` (pure RSS->entries), `is_letter`,
  `slug`, `save_entry` (writes a letter as markdown), thin `fetch_feed`.
- `trading_intel/letters/edgar.py` — `holdings_from_fmp` (pure; parses CVForge/FMP 13F
  rows WITH tickers — no CUSIP->ticker step), `diff_holdings` (pure QoQ, keyed on
  ticker), `parse_infotable` (raw SEC 13F XML fallback).
- `memory/pdf_pipeline.py` — `extract_text` now also reads `.txt/.md/.html` (letters),
  so the whole existing ingest works on them unchanged.
- `memory/models.py` + `alembic/0039_filing_holdings.py` — `filing_holdings` snapshot
  table (unique cik/period/cusip) for the QoQ diff.
- `scheduler/jobs/letters_fetch.py` — poll Substack -> save new letters -> `ingest_folder`
  -> research watchlist + knowledge. Idempotent; local Ollama (`LLM_TAGGING_MODEL`, rule 7).
- `scheduler/jobs/filings_fetch.py` — pull each fund's latest 13F via **CVForge FMP**
  (`cvforge.fmp`), bank `filing_holdings`, diff vs prior, and **surface new/added (with
  tickers) onto the research watchlist** (`watchlist_entries`). `scripts/probe_fmp_13f.py`
  pins the endpoint.
- Tests: `tests/letters/test_substack.py`, `test_edgar.py`, `test_sources.py` (pure).

Rule compliance: research watchlist only, never the options watchlist (rule 4 +
`watchlist-junk-tickers`); local LLM only (rule 7); idempotent upserts (rule 5);
reversible migration (rule 3); no new options vendor (rule 1).

## Verification done

Pure cores executed green: Substack RSS parse (title/date/HTML-strip/`is_letter`), 13F
`parse_infotable` (CUSIP aggregation + sort), `diff_holdings` (new/added/exited), source
dedup. New files `py_compile` clean. Not run against Postgres/Ollama/EDGAR here.

## Deploy

1. **Migrate:** `alembic upgrade head` (applies 0039; round-trip `alembic downgrade -1 ;
   alembic upgrade head`). Laptop -> shared NAS Postgres.
2. **Letters (needs Ollama):** `python -m trading_intel.scheduler.jobs.letters_fetch`.
   Saves new posts under `research/company/letters/<fund>/` and ingests them. Because
   they live under `research/company`, the nightly research ingest also picks them up.
3. **Filings (CVForge FMP — CONFIRMED 2026-07-19, access live/not paywalled):** endpoints
   pinned to `institutional-ownership/dates` (per-CIK filing list) + `institutional-
   ownership/extract?cik&year&quarter` (holdings: `symbol` / `securityCusip` / `value` /
   `shares` / `putCallShare`). Just run `python -m trading_intel.scheduler.jobs.filings_fetch`
   — it does dates -> newest quarter -> extract, drops option legs, and surfaces the
   equity holdings to the research watchlist.
4. **Schedule:** register both in `scheduler/runner.py` (dev) and add NAS DSM tasks —
   `letters_fetch` weekly (e.g. Mon 07:30), `filings_fetch` weekly. Rebuild the NAS image
   `--no-cache` so the new package + jobs bake in.

## Remaining (next increments)

- **Digest summary report** — the "what got digested" one-pager (new letters, new
  tickers, notable 13F moves). The jobs already log `new_symbols`; this rolls it into the
  HTML report pattern. **Top of the list** — it's the summary you asked for.
- **Un-park the sentiment collector** — the 13F probe proved FMP institutional access is
  live on the CVForge tier (it was assumed paywalled). The parked `sentiment_snapshots`
  collector (migration 0034) can likely be revived on the same access — re-run
  `check_sentiment.py` to confirm its endpoints, then re-enable its schedule.
  (13F endpoint + CUSIP->ticker are both already solved.)
- **Website-scrape lane (increment 3)** — per-site parsers for the ~12 public-letter
  sites (Upslope, Vulcan, Fairholme, SVN, Choice, River Oaks, Azvalor, Magallanes…).
- **Dashboard** — the letters/13F feed on the Research Watchlist page.

## Related
`docs/investor_letters_pipeline.md`, `Investor_Letters_Tracker.xlsx`,
`trading_intel/memory/watchlist_ingest.py` (reused).
