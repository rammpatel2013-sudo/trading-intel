"""Options-flow (TAS) aggregation + scorecard.

Pure, source-agnostic DataFrame logic shared by the EOD roll-up job
(``scheduler/jobs/tas_daily_rollup.py``), the scorecard, and the report scripts.
Descriptive flow only — never a trade signal (FlashAlpha rule 4).
"""
