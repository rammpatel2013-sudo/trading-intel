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

## 8. RV/IV box — realized vs. implied (Carroll's framework)

The core question: **is implied vol rich or cheap relative to what the market is
actually doing?**

- **RV21** = trailing 21-day realized vol (annualized close-to-close log returns).
- **VIX − RV21 spread** = variance risk premium in vol points. Wide positive =
  rich premium (vol sellers are being paid). Compressed or negative = realized is
  catching up to implied.
- **VIX3M − RV21 spread** = the same read at the 3-month tenor (VX30 not banked;
  VIX3M is the proxy). A wide 3M spread with a compressed 1M spread = the market
  expects calm to persist even though near-term is choppy.
- **IV/RV ratio** = ATM IV ÷ RV20. >1.2 = rich; <0.9 = cheap; between = fair.

Four-quadrant box:

| | **High IV spread** | **Low IV spread** |
|---|---|---|
| **Low RV** | Rich — premium-selling sweet spot | Calm — quiet tape, cheap hedging |
| **High RV** | Stress — market moving AND priced for it | Cheap — market moving, vol not keeping up |

*Tool: `get_rv_iv_box`. Source: Carroll "Vol Box" framework.*

## 9. Spot-vol beta — is vol over- or under-reacting?

`β = cov(ΔVIX, ΔSPX) / var(ΔSPX)` over a rolling window (default 21d).

- **Normal:** β ≈ −2 to −4 (VIX drops 2–4 pts per +1% SPX move). More negative
  = higher reactivity.
- **Muted** (β near 0): vol barely responding to spot — complacency or a
  mechanically pinned tape (dealer-gamma-driven).
- **Inverted** (β > 0): vol rising WITH spot — the "bullish vol" anomaly (call
  skew bid, squeeze dynamics, or an upside convexity bid in VIX options).

The **residual** (actual ΔVIX − predicted ΔVIX) is the daily gauge:

- Positive residual = "over-vixed" — vol running hotter than spot warrants.
- Negative residual = "under-vixed" — vol lagging, potentially a spring loading.

The 60d beta is pre-banked in `index_skew_daily.vix_spx_beta_60d`; the 21d
version + same-day residual are computed fresh by the cockpit.

*Tool: `get_spot_vol_beta`. Source: spot-vol-beta literature, VolSignals.*

## 10. Implied correlation & dispersion

Implied correlation (COR1M) measures how much the market prices SPX constituent
options as moving together. High = macro-driven; low = single-stock alpha.

Key reads:

- **COR1M percentile (252d):** >85th = extreme correlation (macro fear, hedging
  heavy); <15th = extreme dispersion (stock-picker's market).
- **COR slope** = COR1M − COR3M: positive = near-term correlation stress
  (index hedgers bidding short-dated correlation). Negative = longer-term
  structural correlation.
- **VIXEQ / VIX ratio:** VIXEQ is the equal-weight member VIX. Ratio >1 =
  stock-level vol exceeds index vol → low correlation → dispersion regime.
  Ratio <1 = index vol dominates.
- **DSPX** = Cboe S&P 500 Dispersion Index. Rising DSPX with falling COR1M =
  the classic "dispersion trade" environment.

Carroll's dispersion read: **low COR1M + VIXEQ/VIX > 1** = a loaded spring —
stock-level moves are bigger than the index suggests, and a catalyst can
snap correlation back (suddenly everyone sells together). This is a fragility
indicator, not a directional signal.

*Tool: `get_implied_correlation`. Sources: Cboe COR1M/VIXEQ/DSPX indices,
Carroll dispersion framework.*

## 11. Color — dealer gamma's clock decay (dΓ/dt)

Color tells you **how the gamma map changes just from time passing** — OI, spot,
IV all frozen, only the clock moves. A −$2bn color means dealers will be $2bn
shorter gamma by close than they are now, even if spot doesn't budge.

Why it matters:

- **Negative color** (dealers losing long gamma): the map gets more unstable
  through the day → hedging flows amplify moves later in the session.
- **Positive color** (dealers gaining long gamma): the map gets more supportive →
  moves dampen into the close.
- **Turn time**: if gamma flips sign between now and close, Color reports the
  approximate time — useful for intraday regime transitions.

Color is driven almost entirely by 0DTE and near-term expiries whose theta is
steep. A book heavy in weeklies will show larger Color than one spread across
monthlies.

*Tool: `get_color_at_spot`. Source: VS3D / VolSignals "gamma clock" concept,
greek_profiles engine.*

## 12. Putting it all together (expanded)

The dashboard now provides a multi-layered regime read:

1. **Level & term structure** (§2–3): is vol elevated, and is the shape stressed?
2. **VRP** (§4): is implied richer than realized?
3. **VVIX** (§5): is there uncertainty about the path of vol itself?
4. **Decomposition** (§6): mechanical move or genuine repricing?
5. **RV/IV box** (§8): four-quadrant regime — rich/cheap/stress/calm.
6. **Spot-vol beta** (§9): is vol over- or under-reacting to spot?
7. **Implied correlation** (§10): macro-driven or dispersion regime?
8. **Color** (§11): where is the gamma map heading by close?

The combination is what makes the read sharp. Examples:

- **Carry with a spring:** calm quadrant (low RV, low IV spread) + extreme
  dispersion (COR1M <15th pctile) + negative color. The tape looks quiet but
  gamma is decaying and stock-level moves are larger than the index shows. A
  catalyst can snap everything correlated fast.
- **Genuine risk-off:** stress quadrant + elevated correlation + positive
  spot-vol beta residual (over-vixed) + backwardation + parallel-shift dominated
  decomposition. Full defensive stance.
- **Bullish vol trap:** muted beta (vol barely reacting) + rich IV quadrant +
  call gradient/upside convexity in decomposition + negative color. The tape is
  bid for calls, gamma is decaying, and vol is repricing upside — not fear.

*Reminder: these are descriptive regime reads. Trade signals come only from the
validated `strategies/` modules + the probability model — never from a Greek or a
single decomposition factor on its own.*

---

## Sources
- CBOE, *The VIX Index Decomposition — A Heuristic Framework* (Ed Tom, Aug 1 2025): `https://cdn.cboe.com/resources/vix/VIX-Decomposition-2025-08-01.pdf`
- CBOE VIX Index Decomposition tool: `https://www.cboe.com/tradable-products/vix/vix-decomposition/`
- Doc McGraw posts (`research/doc/`): "The VIX is Lying to You", "Why the VIX Can Rise Without a Full-Blown Panic", "VIX Decomposition Explained", "Shift vs Slide".
- Carroll, "The Vol Box" / RV-vs-IV framework: dispersion + correlation regime classification.
- VolSignals / VS3D: gamma clock / Color concept (`claude/volsignals-methodology.md`).
- Cboe Implied Correlation (COR1M/COR3M), VIXEQ, DSPX indices: `https://www.cboe.com/tradable-products/vix/implied-correlation/`
