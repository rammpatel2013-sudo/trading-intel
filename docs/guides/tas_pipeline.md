# Options time-and-sales (flow) pipeline

Goal: capture the ConvexValue market-wide options tape, store it, and analyze it
to surface unusual flow / hidden catalysts (repeat buying, blocks, sweeps, combo
trades, net directional delta). Descriptive only - rule 4 (a ranked watchlist of
unusual flow, never auto trade signals; any scored alert would move to strategies/).

## Key facts established

- The tas feed is **market-wide, not per-ticker** (each row's `symbol` decodes the
  contract, e.g. `.CVS260702C92` = CVS / 2026-07-02 / call / strike 92).
- It is **live-only**: after the 4pm close the tape returns zeroed trade fields
  (verified on the ConvexValue terminal too), and prior sessions aren't served.
  So the only way to get history is to capture during RTH.
- Access stays in `ConvexClient.time_and_sales(symbol=None, ...)` (rule 1).

## Filter: notional, not size/delta/price

Keep any trade worth **>= $25,000 notional** (price x size x 100). This subsumes
the "$1/contract x 250" idea and is better than alternatives:
- vs price-floor: keeps a 5,000-lot of $0.40 calls ($200k) that a $1 floor drops.
- vs delta filter: a delta cutoff deletes far-OTM lottery bets - which are often
  THE catalyst tell. Notional keeps the big ones, drops the small ones.
Delta is kept on every row as an analysis tag (lotto vs directional vs stock-sub
and for delta-notional), not used to exclude prints.

## Phase 1 - laptop capture (BUILT)

`scripts/tas_capture.py` + `run_tas_capture.bat`. Polls the whole-market tape
every 30s during RTH, dedupes, keeps notional >= $25k, enriches each row with
notional and an inferred buy/sell (price vs bid/ask, recovers it when
aggressor_side is "undefined"), rewrites `data/tas/YYYY-MM-DD.csv` each poll,
stops at 4pm.

Run (DURING MARKET HOURS, 9:30-16:00 ET):
```
python scripts/tas_capture.py                      # notional >= $25k
python scripts/tas_capture.py --min-premium 50000  # whales only
```
CSV columns: time, ticker, strike, expiry, call/put, price, size, notional, side,
iv, delta, gamma, vega, theta, spot.

## Phase 2 - analysis -> Excel (NEXT, after a real CSV exists)

Script reads the daily CSV and builds an Excel workbook:
- premium-by-ticker (biggest flow), repeat contracts (count + premium per line),
- top blocks (single huge prints) and sweeps (same contract/side, near-identical ts),
- combos grouped by timestamp (verticals / risk reversals / straddles via spread_leg),
- net delta-flow by ticker (signed delta-notional), and a composite "unusual" rank
  (opening + aggressive + repeated + short-dated OTM + directional).
- hidden-catalyst heuristic: big directional flow in a name with NO scheduled event.
- cross-reference with our GEX/regime (the unique edge).

## Phase 3 - promote to NAS (LATER, after Phase 1/2 proven)

Laptop-independent (NAS is always on). Steps: a Postgres table + model, a NAS job
(one process open->close, or 1-min one-shots - decide from how busy the tape is),
a DSM task to auto-start at market open Mon-Fri, a 30-day prune of raw prints
(keep the small daily summaries forever), then the `--no-cache` image rebuild.
NAS already has Convex reach, `.env`, and Eastern clock.

## Status

Phase 1 built. **Next action: run it during market hours tomorrow and confirm
non-zero size/premium + real buy/sell, then build Phase 2.**
