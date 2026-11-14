# Desk brief (TailThatWagsDog-style "Equity Desk Brief", rebuilt on our data) — 2026-11-14

Descriptor only (rule 4): nothing writes to `signals`. No AI in the path (rule 7): every sentence is a rule.
Full method + data-gap table: project doc `claude/desk-brief-methodology-20261114.md`.
Paper track of his own picks: project doc `claude/desk-brief-paper-track.md`.

## What runs (NAS DSM, Mon–Fri, ET)
| Time | Job | Writes |
|---|---|---|
| 17:15 | `desk_snapshot` | `desk_und_daily` — Convex `und` screener, 1 call: option volume + bought/sold split for every optionable name. The split resets ~18:00, so this must run before then. |
| 18:30 | `finra_dix` (existing) | now also `finra_short_volume` — every symbol in the FINRA Reg SHO file |
| 18:45 | `equity_daily` (existing) | S&P 1500 bars the brief reuses |
| 19:00 | `desk_brief` | `desk_names` (weekly), `desk_bars`, `desk_gamma_daily`, `desk_picks` (ours), `desk_brief_daily`, page `scripts/cvxmap/data/desk_brief.html` |
One-time: `desk_backfill` (FINRA ~260 sessions, names, last session's brief).

## Definitions (checked against his 11/13 brief)
- **Screen:** optionable, close ≥ $5, 20-day average $ volume ≥ $10M, **1D > +0.5% and 5D < 0** (5D = close vs the close 5 sessions earlier, today included). ETFs go to "fund prints". His liquidity filter is not public — ours is a guess.
- **Dark buy ratio (DBR)** = FINRA short volume ÷ (total − short), summed over 1 / 5 / 10 sessions. Matched all 7 of his two-leg names exactly. **Dark leg passes** = 1W > 1 and 2W > 1 and 1W ≥ 2W (accumulating).
- **BSR** = (close − low) ÷ (high − close) = where it closed in the day's range. Matched AWK 2.5, YETI 10.1, IONS 60.3.
- **Options bullish %** (ours) = (calls bought + puts sold) ÷ split volume, all expiries (Convex `und`). His is short-dated from Signal Sigma (paid) — not the same number. **Options leg passes** > 50%.
- **Short-term gamma** (ours) = naive-sign gamma × OI × 100 × S² × 1%, expiries within 14 days (CVForge snapshot). **Does not match his** (sign disagreed on 3 of his 7 names on 11/13). GEX/Vol = that ÷ the day's $ volume.
- **Two-leg** = dark leg AND options leg. Divergence A = dark passes, options ≤ 50%. Divergence B = options > 50%, dark fails.
- **Top pick** = among two-leg names, short-term gamma > 0 and |GEX/Vol| ≥ 0.0005; most GEX/Vol wins. None → "no top pick today".

## Scoring (fixed before any result)
Entry = open of the session after the brief date; exit = close after 5 / 10 / 20 sessions; equal weight, no stops.
Baskets: top, two-leg, rest of screen, divergence A / B — vs SPY over the same window. Ours and his side by side
(`get_desk_scorecard`; his picks saved with `record_ttwd_picks` or `python -m trading_intel.desk.record`).

## Known limits
- Options leg has no history before the first `desk_snapshot` run; judge it after ~20 sessions.
- FINRA short volume is mostly market-maker inventory; DBR is a crowd-flow descriptor, not proof of buying.
- Price target / rating are not served on our FMP tier → "n/a".
