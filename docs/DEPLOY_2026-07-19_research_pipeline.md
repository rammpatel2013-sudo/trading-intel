# Investor-letters → research-report pipeline — full NAS automation (2026-07-19)

The whole chain runs unattended as NAS DSM tasks — pull letters → ingest → update the
research watchlist → pull FMP/CVForge data → build the report. **No manual step** (FMP
fields self-adapt; no probe needed). One-time deploy below, then hands-off.

## The chain (each an idempotent job)

| # | Job | What it does | Feeds |
|---|-----|--------------|-------|
| 1 | `letters_fetch` | Poll Substack feeds → save new letters → ingest → tickers + rationale onto the **research watchlist** (`watchlist_entries`) + knowledge | 2,3 |
| 2 | `filings_fetch` | Pull each fund's latest 13F via **CVForge FMP** → bank `filing_holdings` → diff QoQ → new/added names (with tickers) onto the research watchlist | 3 |
| 3 | `research_report` | For each research-watchlist ticker: CVForge OHLC (4h/daily/weekly → **stage**) + FMP fundamentals/institutional/analyst + transcript + the banked **letter commentary** → `reports/<SYM>_research_<date>.html` | — |

New code: `trading_intel/research/` (`stage.py` Weinstein classifier — tested;
`enrich.py` self-adapting FMP getters — tested), `scheduler/jobs/research_report.py`,
`scheduler/jobs/letters_fetch.py`, `scheduler/jobs/filings_fetch.py`. Reuses the existing
`watchlist_ingest`, `earnings.transcripts`, and `clients.cvforge` (rule 1). Migrations 0039.

## One-time deploy

1. **Migrate:** `alembic upgrade head` (0039 `filing_holdings`; round-trip check). Laptop → shared NAS Postgres.
2. **Push + rebuild the NAS image `--no-cache`** (bakes the new `research/` package + 3 jobs) from the GitHub tarball, per the standard `DEPLOY_*` pattern.
3. **Add 3 DSM tasks** (User: root; NAS clock America/New_York), calling `run_job.sh`:

   ```bash
   # a. Letters — weekly, Mon 07:30
   bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh letters_fetch
   # b. 13F filings — weekly, Mon 07:45 (CVForge FMP; runs before the report)
   bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh filings_fetch
   # c. Research reports — weekly, Mon 08:15 (after a+b so the watchlist is fresh)
   bash /var/services/homes/drmithil/trading-intel/scripts/nas/run_job.sh research_report
   ```

That's it — after this the reports regenerate weekly with zero input.

## Verify
```bash
tail -20 ~/ti_letters_fetch.log ~/ti_filings_fetch.log ~/ti_research_report.log   # EXIT 0
ls -lt /var/services/homes/drmithil/trading-intel/reports/*_research_*.html | head
```
Manual one-off any time: `python -m trading_intel.scheduler.jobs.research_report TAP ORCL`.

## Notes / follow-ups (non-blocking)
- **Tone**: `research_report` shows the latest transcript; wire the earnings-inflection
  detector's QoQ Δtone into the transcript panel when convenient.
- **Options-vol fusion**: the report links to `ticker_report.py <SYM>`; merging the two into
  one page = expose `ticker_report.build()`'s HTML and prepend the research panels.
- **Un-park sentiment**: the same CVForge FMP access powers the parked `sentiment_snapshots`
  collector — re-enable it on this deploy.
- **Digest summary**: `Investor_Letters_Digest_*.html` is the weekly cross-fund one-pager;
  add a `letters_digest` job to regenerate it from `watchlist_entries` + `filing_holdings`.

## Related
`docs/investor_letters_pipeline.md`, `docs/ticker_research_report_plan.md`,
`docs/DEPLOY_2026-07-19_letters.md`, `Investor_Letters_Tracker.xlsx`.
