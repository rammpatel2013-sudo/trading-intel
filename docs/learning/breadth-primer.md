# Market breadth & the synthesis engine — primer

Reference for the breadth collector (`scheduler/jobs/breadth.py`), the Bull/Bear-Line
regime read, and the synthesis engine (`synthesis/market_read.py`). Descriptive
context only (FlashAlpha rule 4). To make this searchable in graphify /
`search_knowledge`, drop a **docx/pdf** copy into `research/doc/` and run
`python -m trading_intel.memory.sync_knowledge` (`.md` is NOT ingestible — see the
vol-newsletter KB rule).

## What each breadth measure means
- **Advance-Decline (A-D) line** — the cumulative running sum of daily (advancers −
  decliners). Its *level* is arbitrary; its *direction and divergence vs price* are
  the signal. A rising A-D line = participation broadening; a rolling-over A-D line
  while the index still climbs = participation narrowing.
- **% above the 50 / 200-day MA** — the share of names in an uptrend. Rules of thumb:
  **< 40% = weak / washed-out**, **> 70% = strong / can be frothy**. The 50-day is
  the tactical read, the 200-day the trend read.
- **New highs − new lows (52-week)** — expansion (many new highs) confirms a healthy
  advance; a surge in new *lows* while the index holds up is an internal fracture.
- **McClellan Oscillator** = EMA19(net adv) − EMA39(net adv); **Summation Index** =
  running sum of the oscillator. Oscillator **> +50** strong thrust / **< −50** weak;
  the Summation Index's slope is the intermediate-trend read.

## Divergence — the "gap" (Norseman's core)
A market top is a *process*, not a price: breadth rolls over *before* price. The
**duration** of the gap between a breadth peak and the price peak is the warning.
Historical scale at major tops: **2007 ≈ 4–5 months, 2000 ≈ 21 months, 1929 ≈ 24
months**. Our `breadth_divergence` returns `{state, length}`:
- **confirming** — price and the A-D line both make the high → no gap, healthy.
- **bearish_div** — price makes a new high, A-D line does NOT → the top-warning gap;
  `length` = how many sessions it has held.
- **bullish_div** — price makes a new low, A-D line firmer → washout / bottoming.

## The Bull/Bear Line (Norseman regime floor)
`bull_bear_line = 0.90 × running-max WEEKLY close` (SPX-equivalent, computed off our
maintained SPY series ×10). It **ratchets up with every new weekly-closing high,
never down**. Price above it = cyclical bull ("engine running"); a **weekly close
below it** = a 10%-close "test". One test a bull can survive; **two ten-percent
closes = terminal** (no bull since 1940 has survived two), and a second can't occur
until ~6 months (≈session 128) after the first. See [[norseman-methodology]].

## How the synthesis engine fuses it (the four pillars)
The engine never averages the signals — it narrates their *interaction*:
1. **REGIME** — Bull/Bear-Line side × breadth confirmation/divergence.
2. **MECHANICS** — dealer gamma sign (long = pin/dampen, short = amplify), the gamma
   flip, expected-move rails.
3. **WEATHER** — VIX level, VIX **term structure** (9d vs 3m: contango = calm,
   backwardation = stress), and **VVIX** vol-of-vol (elevated = fragile even if VIX
   is low).
4. **TAPE** — demand-led vs written (the ΔOI+ΔIV / aggressor read).

### The cross-pillar rules it encodes
- **Narrow breadth (%>200DMA < 40) + short gamma = fragility multiplier** — a small
  catalyst produces an outsized move.
- **Long-gamma pin + elevated VVIX = coiled spring** — the pin suppresses realized
  vol while tail demand builds, so it gaps when the pin breaks.
- **Backwardated VIX term + price at the call wall with vol offered = the pin holds
  despite the fear** (mechanics beat sentiment short-term).
- **Bull/Bear-Line floor sitting on a put wall / high-gamma strike = double support.**
- **Breadth diverging (bearish_div) under a long-gamma pin = the classic top setup** —
  the tape stays quiet while internals rot; watch the Bull/Bear-Line floor.

Output = the path of least resistance + a levels ladder + if-then triggers (ours +
the newsletter authors' scenarios) + a confluence score + one narrative line. It's a
description of the board, not a trade signal.
