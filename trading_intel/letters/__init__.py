"""Investor-letter + 13F-filing ingestion.

Fetch investor letters (Substack RSS) and fund 13F holdings (SEC EDGAR), then feed
them through the EXISTING research pipeline: letters are saved as text ``Document``s
that ``memory.watchlist_ingest`` turns into ``watchlist_entries`` (the RESEARCH
watchlist) and the knowledge/chunk pipeline indexes; 13F holdings are diffed
quarter-over-quarter and surfaced the same way.

Design + source list: ``docs/investor_letters_pipeline.md`` and
``Investor_Letters_Tracker.xlsx``. Extracted tickers are descriptive research context
only, never a trade signal (FlashAlpha rule 4), and land on the research watchlist —
never the options collection watchlist (MEMORY ``watchlist-junk-tickers``).
"""
