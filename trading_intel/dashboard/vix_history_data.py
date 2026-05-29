"""Daily history of the CBOE volatility-term indices from yfinance (free).

Pulls daily closes of:

- ``^VIX``    - 30-day VIX (the headline index)
- ``^VIX9D``  - 9-day VIX (near-term stress)
- ``^VIX3M``  - 3-month / 90-day VIX
- ``^VIX6M``  - 6-month VIX

Returns one DataFrame with columns ``date, vix, vix9d, vix3m, vix6m``. yfinance
returns business days; missing days are simply absent (the chart line will skip).
Per-ticker errors are swallowed so one bad symbol doesn't kill the whole pull;
the failing tenor's column is left empty. Pure - no Streamlit, no DB. Descriptive
view (FlashAlpha rule 4); never writes a signal.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

_TENORS = {
    "vix": "^VIX",
    "vix9d": "^VIX9D",
    "vix3m": "^VIX3M",
    "vix6m": "^VIX6M",
}


def fetch_vix_term_history(days: int = 180) -> pd.DataFrame:
    """Daily closes for each VIX tenor over the last ``days`` calendar days."""
    import yfinance as yf

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=max(days, 5))
    cols: dict[str, pd.Series] = {}
    for col, ticker in _TENORS.items():
        try:
            hist = yf.Ticker(ticker).history(
                start=start, end=end, interval="1d", auto_adjust=False,
            )
        except Exception:
            hist = None
        if hist is None or hist.empty or "Close" not in hist.columns:
            cols[col] = pd.Series(dtype=float)
            continue
        close = pd.to_numeric(hist["Close"], errors="coerce").dropna()
        close.index = pd.to_datetime(close.index).date
        cols[col] = close
    if not cols:
        return pd.DataFrame(columns=["date", *_TENORS.keys()])
    frame = pd.DataFrame(cols).sort_index()
    frame.index.name = "date"
    return frame.reset_index()
