# Reading the VIX & Volatility Dashboard

*A practical interpretation guide for the `8_VIX` dashboard. Grounded in the CBOE
"VIX Index Decomposition" whitepaper (Aug 2025, Ed Tom) and the Doc McGraw
volatility-structure posts in `research/doc/`. Descriptive regime reads only —
nothing here is a trade signal (FlashAlpha rule 4).*

---

## 1. First principle: the VIX is a calculation, not a mood

The VIX is the weighted-average implied volatility across the active SPX strike
spectrum of the two expiries bracketing 30 days. It is a **pricing output**, not
a sentiment survey. So "VIX up" does not automatically mean "fear up" — it means
the options market marked the cost of 30-day optionality higher. The job of this
dashboard is to tell you *why* it moved: mechanical math vs. genuine repricing of
risk.

## 2. Level & regime zone (the headline)

| Zone | VIX | Read |
|---|---|---|
| Carry | < 22 | Vol-selling environment; premium is the edge |
| Fragility | 22–32 | Transitional, unstable |
| Stress | > 32 | Risk-off; crisis territory ~38+ |

The level alone is a blunt instrument. Everything below is what makes it sharp.

## 3. Term structure — the *shape* tells the story

Tenors: **VIX9D → VIX (30d) → VIX3M → VIX6M.**

- **Contango (upward slope, near < far):** calm. The market expects today's vol to
  fade. Normal state.
- **Backwardation (downward slope, VIX9D > VIX > VIX3M):** acute front-end stress —
  the market is paying up for protection *right now*.
- **VIX9D vs spot VIX:** the fastest near-term stress gauge. VIX9D pushing above
  VIX is an early backwardation warning.

*Example (2026-05-24): 14.07 → 16.76 → 20.03 → 22.35 — healthy contango, consistent
with the "carry" zone.*

## 4. VRP (Variance Risk Premium) — the mechanical-vs-fear baseline

`VRP = VIX − SPX 20-day realized vol` (in vol points).

- **Positive (normal):** implied richer than realized — vol sellers are being paid
  to carry risk. Wide VRP = rich premium.
- **Compressing toward zero / negative:** realized vol is catching up to implied —
  the market is actually moving as much as it's pricing. A stress tell, even if the
  VIX level still looks low.

*Example: VRP ≈ +6 (VIX 16.76 vs ~10.8% realized) — comfortable positive premium.*

## 5. VVIX — vol of vol (the confirmation lens)

VVIX is the implied vol of VIX options — the market's uncertainty about the *path*
of volatility itself. A VIX move backed by a rising VVIX is more likely a genuine
instability than a mechanical drift. Watch the **VVIX/VIX ratio**: an elevated
ratio at a low VIX (as on 2026-05-24, ~91/16.8 ≈ 5.4) says the market is paying up
for vol-of-vol even while spot vol is calm — latent fragility under a quiet tape.

## 6. The VIX Decomposition — mechanical vs. true fear (CBOE 6-factor)

This is the centerpiece. CBOE splits a day's VIX change into six components by
perturbing a synthetic 30-day fixed-strike skew one factor at a time. Read them by
*which factor dominates*:

| Factor | Skew region | What it means |
|---|---|---|
| **1. Sticky Strike** (expected move) | belly (40–50Δ) | **Mechanical.** ATM rode along yesterday's fixed skew as spot moved. Dominates ~88% of moves when VIX < 15. A VIX move that's mostly this = *false alarm*, no new fear. |
| **2. Parallel Shift** | belly | **True fear / regime.** The whole surface repriced up (or down) at once. An up-shift = wholesale bid for protection after a 2σ+ shock — a regime signal that can persist for weeks. |
| **3. Put skew gradient** | shoulders (15–45Δ puts) | Excess demand for OTM downside hedges beyond the parallel move. |
| **4. Call skew gradient** | shoulders (15–45Δ calls) | Excess demand for upside calls — drives "spot up, vol up." |
| **5. Downside convexity** | wings (1–15Δ puts) | Bid for crash/tail insurance. |
| **6. Upside convexity** | wings (1–15Δ calls) | Bid for levered upside / performance chasing (e.g. the Liberation-Day rally setup). |

### How to read the mix
- **Sticky-strike dominated:** the move is plumbing, not fear. Don't reflexively
  hedge. ("You walked to a pricier diner; the menu didn't change.")
- **Parallel-shift dominated:** the market is repricing the *baseline* cost of
  risk. This is the real regime tell. Up-shift = genuine risk-off; down-shift
  (rarer, only in high-vol regimes mean-reverting) = the fear bleeding out.
- **Convexity skew (down vs up):** tells you *positioning*. A downside-convexity
  bid = bearish tail hedging; an upside-convexity bid alongside a VIX rise = bulls
  levering into a rally (CBOE's Liberation-Day case, which preceded a +14% 30-day
  move).
- **Spot up, vol up / spot down, vol down** (positive co-movement, ~20% of days):
  almost always a parallel shift — the market changing its mind about baseline risk
  independent of direction.

## 7. Putting it together — toward a swing-trade bias

The dashboard is a *regime read*, and regime sets the bias the strategy layer then
acts on:

- **Calm, sellable regime:** contango term structure + wide positive VRP +
  sticky-strike-dominated decomposition. Premium-selling favored; trend
  continuation more reliable.
- **Genuine risk-off:** backwardation + a parallel up-shift + a downside-convexity
  bid + compressing VRP. Defensive; long-vol/hedges favored; mean-reversion bounces
  are lower-confidence.
- **Bullish vol (the trap):** VIX rising but driven by call gradient + upside
  convexity (not parallel shift / put wings). "Spot up, vol up" — chasing, not fear.

*Reminder: these are descriptive regime reads. Trade signals come only from the
validated `strategies/` modules + the probability model — never from a Greek or a
single decomposition factor on its own.*

---

## Sources
- CBOE, *The VIX Index Decomposition — A Heuristic Framework* (Ed Tom, Aug 1 2025): `https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf`
- CBOE VIX Index Decomposition tool: `https://www.cboe.com/tradable-products/vix/vix-decomposition/`
- Doc McGraw posts (`research/doc/`): "The VIX is Lying to You", "Why the VIX Can Rise Without a Full-Blown Panic", "VIX Decomposition Explained", "Shift vs Slide".
