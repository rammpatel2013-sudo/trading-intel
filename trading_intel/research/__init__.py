"""Cross-domain research report enrichment.

Pulls the non-options context for the single-ticker research report — Weinstein stage
analysis (from CVForge OHLC aggs), FMP fundamentals / institutional ownership / analyst
consensus, earnings transcript + tone, and investor-letter commentary — so the existing
options-vol dashboard (`scripts/ticker_report.py`) can be extended into a full one-pager.

Descriptive research context only, never a trade signal (FlashAlpha rule 4). All vendor
access funnels through `clients/cvforge.py` (rule 1).
"""
