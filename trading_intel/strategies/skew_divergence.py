"""Skew-divergence bounce rule for single stocks — rule sd-v1, frozen 2026-11-13.

A stock that has dropped hard and is still making new lows while option traders STOP paying up for
downside protection (25-delta risk reversal falling fast, i.e. skew moving toward calls) has tended
to bounce over the next month.

Rule sd-v1 (all three on the same session; stocks only):
  1. close >= 10% below its 20-session high
  2. close at a 10-session low
  3. 5-session change in RR (25d put IV - 25d call IV, 30-day) in the bottom 10% of its own
     trailing 252 sessions (>= 120 observations needed)
First firing per name, then a 10-session cooldown. Hold ~20 sessions.

Evidence (project doc ``claude/skew-leading-indicator-study-20261110.md``; ledger section F):
  8 mega-caps 2019-06 -> 2026-11, 26 episodes: 20 sessions +9.8% vs +3.4% for other selloff days,
  80% up; local projections with same-day clustering corrected +8.1% extra (t 2.9); still works
  with SPY below its 200-day average. Nothing in the first 10 sessions. ETFs: weak (t < 2).
  Drop from the ALL-TIME high instead of the 20-day high has no edge.

Pure functions only (no IO). On-demand read via MCP ``run_skew_scan``; nothing is written to the
``signals`` table (that stays reserved for scheduled strategies, rule 4).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RULE_VERSION = "sd-v1 (frozen 2026-11-13)"
DROP_FROM_HIGH = -0.10
HIGH_LOOKBACK = 20
LOW_LOOKBACK = 10
SKEW_CHANGE_SESSIONS = 5
SKEW_PCT_MAX = 0.10
NEAR_PCT_MAX = 0.25
PCT_WINDOW = 252
PCT_MIN_OBS = 120
COOLDOWN = 10
HOLD_SESSIONS = 20

EVIDENCE = (
    "Backtest 2019-2026 on 8 mega-caps: 20 sessions after a signal +9.8% vs +3.4% for other "
    "selloff days, 80% went up (about +8% extra after corrections, t 2.9). The gain comes in "
    "sessions 10-20, so hold about a month. Proven on large stocks only; ETFs showed no real edge; "
    "the backtest did not skip earnings."
)


def trailing_pct(x: np.ndarray, window: int = PCT_WINDOW, min_obs: int = PCT_MIN_OBS) -> np.ndarray:
    """Percentile of each value within its own trailing window (inclusive), no look-ahead."""
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        if np.isnan(x[i]):
            continue
        h = x[max(0, i - window) : i + 1]
        h = h[~np.isnan(h)]
        if len(h) >= min_obs:
            out[i] = (h < x[i]).mean() + 0.5 * (h == x[i]).mean()
    return out


def evaluate(close: pd.Series, rr: pd.Series) -> pd.DataFrame:
    """Per-session read. ``close`` = split-adjusted closes indexed by session date (ascending);
    ``rr`` = RR in vol points on the same index (NaN where missing)."""
    df = pd.DataFrame({"close": close.astype(float)})
    df["rr"] = rr.reindex(df.index).astype(float)
    df["off_high"] = df["close"] / df["close"].rolling(HIGH_LOOKBACK, min_periods=1).max() - 1
    df["at_low"] = df["close"] <= df["close"].rolling(LOW_LOOKBACK, min_periods=1).min()
    df["rr_chg"] = df["rr"] - df["rr"].shift(SKEW_CHANGE_SESSIONS)
    df["rr_chg_pct"] = trailing_pct(df["rr_chg"].to_numpy())
    pct_ok = df["rr_chg_pct"].le(SKEW_PCT_MAX).fillna(False)
    df["fire"] = df["off_high"].le(DROP_FROM_HIGH) & df["at_low"] & pct_ok
    ep = np.zeros(len(df), bool)
    last = -(10**9)
    for i in np.where(df["fire"].to_numpy())[0]:
        if i - last > COOLDOWN:
            ep[i] = True
        last = i
    df["episode"] = ep
    return df


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def status(df: pd.DataFrame) -> dict:
    """Plain-words read of the latest session plus any episode still inside the hold window."""
    last = df.iloc[-1]
    n = len(df)
    eps = [i for i in np.where(df["episode"].to_numpy())[0] if n - 1 - i < HOLD_SESSIONS]
    rr_ok = bool(df["rr"].iloc[-PCT_WINDOW:].notna().sum() >= PCT_MIN_OBS)
    pct = last["rr_chg_pct"]
    cond = {
        "10%+ below 20-day high": f"{'yes' if last['off_high'] <= DROP_FROM_HIGH else 'no'} "
        f"({_pct(last['off_high'])})",
        "at a 10-day low": "yes" if bool(last["at_low"]) else "no",
        "skew moving to calls (5-day change in bottom 10% of the year)": (
            "no skew data"
            if pd.isna(pct)
            else f"{'yes' if pct <= SKEW_PCT_MAX else 'no'} ({pct * 100:.0f}th percentile)"
        ),
    }
    out = {
        "as_of": str(df.index[-1]),
        "close": round(float(last["close"]), 2),
        "conditions": cond,
        "fired_today": bool(last["fire"]),
        "enough_skew_history": rr_ok,
    }
    if eps:
        i = eps[-1]
        out["signal"] = {
            "fired_on": str(df.index[i]),
            "sessions_ago": int(n - 1 - i),
            "sessions_left_in_hold": int(HOLD_SESSIONS - (n - 1 - i)),
            "off_high_on_signal": _pct(df["off_high"].iloc[i]),
            "skew_change_pct_on_signal": f"{df['rr_chg_pct'].iloc[i] * 100:.0f}th percentile",
            "move_since_signal": _pct(last["close"] / df["close"].iloc[i] - 1),
        }
        sig = out["signal"]
        out["verdict"] = (
            "READY - fired today"
            if i == n - 1
            else f"READY - fired {sig['sessions_ago']} sessions ago, "
            f"{sig['sessions_left_in_hold']} left in the 1-month hold"
            + (" (still firing today)" if out["fired_today"] else "")
        )
    elif (
        last["off_high"] <= DROP_FROM_HIGH
        and bool(last["at_low"])
        and not pd.isna(pct)
        and pct <= NEAR_PCT_MAX
    ):
        out["verdict"] = "CLOSE - price conditions met, skew shift just short of the cutoff"
    elif not rr_ok:
        out["verdict"] = "CAN'T SCORE - not enough skew history (thin options or new listing)"
    else:
        missing = [k for k, v in cond.items() if not v.startswith("yes")]
        out["verdict"] = "NOT READY - missing: " + "; ".join(missing)
    return out
