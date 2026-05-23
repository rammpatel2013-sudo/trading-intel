# Company research drop folder

Drop company / equity research PDFs or .docx files here, then run:

    python scripts/sync_research_watchlist.py

The local LLM (Ollama) reads each NEW file, extracts the tickers it discusses
(with a rationale + sentiment), adds them to the dynamic watchlist
(`watchlist_entries`), and backfills daily price history for the newly
discovered tickers. From the next collector cycle on, those tickers are part of
the *effective watchlist*, so they get the full regime-data collection
(GEX/DEX, flow, walls, etc.) and appear on the dashboard.

Files already ingested (same content hash) are skipped, so re-running is safe.
Research material may be proprietary — this folder is gitignored.
