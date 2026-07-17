"""Sentiment descriptors — institutional 13F + analyst ratings/targets (CVForge FMP).

Pure ``compute`` (value object + derivations) + tolerant ``fmp_map`` (payloads ->
inputs). The FMP pull + persistence live in ``scheduler/jobs/sentiment.py``.
"""

from trading_intel.sentiment.compute import (
    DERIVED_FIELDS,
    RAW_FIELDS,
    SentimentInputs,
    derived_fields,
)
from trading_intel.sentiment.fmp_map import extract_inputs

__all__ = [
    "DERIVED_FIELDS",
    "RAW_FIELDS",
    "SentimentInputs",
    "derived_fields",
    "extract_inputs",
]
