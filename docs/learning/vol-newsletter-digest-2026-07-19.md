# Vol-newsletter digest — Yamco 07/13 + 07/20 + Risk-Management note (2026-07-19)

Source: Yamco / yamtrades Substack (paid), three notes — "Positioning and Week Ahead
[07/13]", "[07/20]", and "Risk Management & Day Trading" (Jun 11). Data-driven index /
single-name vol desk. Companion to `docs/learning/vol-newsletter-digest-2026-07-11.md`
and the [[vol-newsletter-sources]] memory. Risk methodology extracted separately to
`docs/playbooks/risk_management.md`.

## The positioning arc (07/13 -> 07/20)

Both weeks tell one story: **realized vol collapsing into the 07/20–07/23 window**,
under a calm index with violent single-name rotation beneath it.

- **RV collapse -> VCF inflows.** Both notes: "RV drops normally bring about Vol
  Control Fund inflows (systematics that invest on **1m trailing RV**)." By 07/20:
  "VCFs generally hitting **peak exposure into 07/23**." The weekly SPX straddle went
  82 (07/13) -> 120 (07/20) as earnings approached.
- **The dispersion complex at extremes.** COR1M (1-month implied correlation) at/near
  record lows; dispersion at record highs; VIXEQ/VIX (single-name 30d vol ÷ index vol)
  at record highs. Translation: the index is asleep while individual names swing —
  "a headline can break something, but you need the headline."
- **Momentum unwind.** Per Goldman, the momentum de-gross over the prior week was "the
  most extreme since COVID." Yamco's read: not a standalone crash signal, but a
  **powder keg** — the match is a sharp index move.
- **The powder-keg mechanic (07/20, "Realized Vol Degrossing").** Low RV + VCFs at peak
  exposure is asymmetric: a spike in RV forces **risk-parity de-grossing** (each asset
  contributes equal risk, so a vol spike in one triggers de-levering / margin-driven
  selling across the book), which is the classic **correlation-to-1** cascade. So the
  same inverse-vol machinery that bids a falling-RV tape sells hard when RV turns.

## What this confirms in our stack

- **vol_control = 1m trailing RV.** The notes state the convention explicitly, which is
  exactly the `rv_21` relabel we just shipped (`flows/registry.py`) and what
  `get_vol_control_flow` computes (`window=21`). Independent confirmation of the
  descriptor's core assumption. See [[em-break-followups]].
- **Both signs already modelled.** `flows/descriptors.cohort_flow` gives buying on
  falling RV and selling on rising RV, and `exposure_convexity` (`dw/drv = -target/rv²`)
  captures why the last leg of a vol-down move bids hardest — the note's "peak exposure
  into 07/23." The degrossing/correlation-to-1 cascade is the negative-RV-shock tail of
  the same function.
- **Dispersion / COR1M already built** (`get_index_skew` dispersion fields + rv_rolloff,
  2026-07-11 — don't re-add). Momentum factor already in the factor layer.

## Actionable (ranked, not yet built)

1. **VCF degross-risk / peak-exposure asymmetry read.** Cheapest, highest-signal. We
   already compute `w = vol_control_exposure(rv)`; surface **how close w sits to
   `w_max`** (exposure percentile / `near_peak` flag) on `get_vol_control_flow`. When
   exposure is pinned near the cap AND RV is at lows, the note's asymmetry is live: the
   flow read should carry a "downside-asymmetric / degross-risk" tag. No new data — pure
   addition to the existing descriptor.
2. **VIXEQ/VIX ratio** (mean single-name 30d IV ÷ index 30d IV). A clean dispersion /
   micro-vs-macro-vol gauge the notes lean on at "record highs." Check whether the
   dispersion fields already expose it; if not, it's derivable from data we bank
   (per-name IV via `iv_tenor` / `vol_richness` vs index IV).
3. **Multi-anchor implied range.** The notes quote straddle ranges to Weekly / month-end
   / OPEX / QOPEX with 50/70% bands. We already snapshot surface DTE anchors and price
   straddles; banking the index implied range to each anchor gives a "where's the market
   pinned / where does it break" band for the dashboard.
4. **NAAIM + AAII positioning/sentiment.** Free, weekly (NAAIM active-manager equity
   exposure Wed; AAII Thu). Distinct from the **parked** FMP sentiment collector (FMP
   paywalled — [[trend-collection-buildout]]); these two are scrapeable and would revive
   the sentiment lane without the paywall.
5. **S5FD breadth oscillator** (% of S&P above short MA; oversold 15–30 / overbought
   70–90). Minor; a breadth confirm for the technicals page.

**How we use it - pressure, not $.** The usable signal is the *direction and intensity*
of the systematic bid/offer read straight off the RV/IV/HV roll-off: **falling vol =
buying pressure, rising vol = selling pressure**, and `exposure_convexity` (dw/drv) says
that pressure *accelerates* as vol nears its lows (why the last leg of a vol-down move
bids hardest). All of this is **AUM-free**: it is the sign of `d_exposure`, the
`direction` field, and a cross-sectional / banked **percentile** of the magnitude - never
a dollar figure. We will not have VCF AUM, so `total_buying_usd` stays unused and the
`flows/registry.py` AUM calibration is **moot** - consume the read as pressure/direction
only. (RV is the note's VCF trigger, 1m trailing; IV falling via `iv_tenor` /
`vol_richness` and HV falling via `prices/realized_vol` corroborate the same "vol coming
out -> systematic re-risking" read.)

## Monte-Carlo RV forecast (07/20 p.22-24) - his version of our `rv_rolloff`

The chart the note leans on for "where does RV go from here" is an explicit **Monte-Carlo
RV forecast**, and it is the *same mechanic* as
`prices/realized_vol.rv_rolloff_projection` - generalized from one path to a distribution:

- **Method (as captioned):** 2,000 simulated paths; each forward day's close-to-close
  return is **bootstrapped from the full distribution of the last 252 trading days**; 21D
  *and* 63D RV are recomputed along each path, so the mechanical roll-off (recent big
  moves aging out of the trailing window) is carried through. Output is the **median
  simulated RV (dashed) + a P5-P95 band**, over the next 5 / 10 / 15 sessions.
- **Us:** `rv_rolloff_projection` already does the "hold the window, roll it forward,
  recent returns age out, measured RV drifts toward a floor" step - but as a **single
  deterministic path** (`future_return=0`, calm-tape). His is that same step wrapped in a
  bootstrap Monte-Carlo: a median path + a P5-P95 cone instead of one line.
- **Why it matters for the pressure read:** the **slope of the median path** is the
  systematic buy/sell pressure direction (falling RV -> buying), and the **P5-P95 band**
  carries both the confidence and the upside tail (an RV spike -> the degross /
  correlation-to-1 risk). All AUM-free - pure RV dynamics.
- **Also (p.22):** his VCF-exposure framework ("Risk Control 2.0 VAF") is built on
  **21D/120D** RV, not a single window - noted as knowledge; not changing the `rv_21`
  label per your call.
- **If ever built (not now):** a small extension to `rv_rolloff_projection` - a
  bootstrap-path mode returning median + percentile bands. No new data.

## Risk methodology

The Jun-11 note is trading discipline, not positioning — extracted to
`docs/playbooks/risk_management.md`. The one portable, system-relevant idea is
**scaled exits** (T1/T2/T3 partial profit-taking with stop-to-breakeven after T1),
now available in the backtest as `backtest/em_break.scaled_exit_r` so the P6 validation
can report a blended R that reflects how a defined-risk structure is actually managed,
not just a binary target/stop.
