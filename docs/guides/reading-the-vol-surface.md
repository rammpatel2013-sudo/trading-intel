# Reading the Volatility Surface (and writing the surface + flow report)

*Interpretation guide for the Vol Lab and the surface/flow report writer. Grounded
in the skew/vol-trading sources in `research/` (Riding on a Smile; Santander
Volatility Trading Primer; Trading Volatility; Forecasting IVS Dynamics), the
charm/gamma/0DTE-hedging notes, and the house report style. Descriptive regime
reads only - FlashAlpha rule 4; trade signals come from `strategies/` + the
probability model, never from a surface read alone.*

---

## 1. The two axes

A surface is IV over **moneyness/strike** (the smile/skew) and **tenor/DTE** (the
term structure). Read both:

- **Term structure** = ATM IV vs tenor. **Contango** (up-sloping, near < far) =
  calm; the market expects today's vol to fade. **Backwardation** (inverted, near
  > far) = acute near-term stress. The **forward vol** between two expiries is the
  vol implied for that gap (negative forward variance => calendar-arb / data noise).
- **Skew / smile** = IV vs strike within a tenor. Equity skew is a **smirk**: OTM
  puts carry richer IV than OTM calls (the structural crash bid). Skew is usually
  **steepest in the front month** and flattens out the curve.

## 2. Anatomy (the vocabulary the report uses)

- **Crash bid / put wing** - deep-OTM put IV, elevated and roughly flat across
  tenors (e.g. "~22-24% across every tenor, front-month -10% strike hottest at
  23.9%"). This is the institutional hedge bid.
- **Call wing** - OTM call IV, low for short-dated (e.g. ~12%) drifting up at the
  back end (~14%).
- **ATM term structure** - e.g. "contango from 14.8% at the 8-45d front to 17.1%
  past six months."
- **Risk reversal (RR)** - the skew steepness number: `IV(OTM put) - IV(OTM call)`
  at a fixed % or delta (e.g. 5% strikes, or 25-delta). "5% RR runs 7.3 vol pts at
  8-45d, compressing to 5.1 at 181-365d" => front skew steeper than back.

## 3. The numbers to pull each day

ATM IV per tenor + the contango/backwardation slope; the **25-delta (or 5%/10%)
risk reversal** per tenor and front-vs-back; the **wing levels** (deep-OTM put IV =
crash-bid level, call-wing IV); the **forward vol**; and the **IV-HV spread**
(below). These are exactly the read-outs the Vol Lab renders.

## 4. The dealer-hedging lens (gamma / vanna / charm)

The surface is shaped by dealers hedging their books - reading it well means
reading their hedging:

- **Gamma** - positive (dealers long gamma) => hedging *dampens* moves, pins toward
  walls (mean-reverting); negative => hedging *amplifies* (trending, gaps run). See
  the gamma-regime classifier.
- **Vanna** - delta's sensitivity to IV. Vanna hedging couples spot and vol: as IV
  falls, dealers long vanna sell into strength (and vice-versa) - a key driver of
  "spot up, vol down" grinds.
- **Charm** - delta's decay over time. Charm-driven re-hedging is **front-loaded
  into the close and into expiry**, so it dominates **0DTE** intraday. You cannot
  "sum charm at a point" - it is time-of-day dependent: small midday, large into
  the bell. 0DTE tape = gamma + charm; watch the **inflection zones** where dealer
  hedging ramps.

## 5. IV-HV spread - rich vs cheap (the screener)

`spread = ATM IV - realized vol (HV)`, per window. We have ATM IV (from the term
structure) and realized vol (`rv20` ~= 1-month, `rv60` ~= 3-month) stored, so:

- **30d:** IV(~30d) - HV(rv20).  **60d:** IV(~60d) - HV(rv60).
- **Positive & wide** => options priced richer than the stock actually moves =>
  **premium-selling edge** (sell vol / spreads). This is the variance-risk-premium
  (IVAR) the vol-trading sources lean on: realized usually underprints implied.
- **Negative** => options **cheap** vs realized => long-vol / calendars / debit
  structures favored.
- **Rank the universe:** top = richest (sell-vol candidates), bottom = cheapest
  (buy-vol candidates). Complement with **IV Rank / IV Percentile** (current IV vs
  the name's own 52-week range) so you know if "rich" is rich for *that* name.

## 6. Read -> bias (descriptive)

- **Steep put skew + high IV-HV (rich)** => put-spread / iron-condor selling; the
  skew pays you, realized lags implied.
- **Flat skew + negative IV-HV (cheap)** => long vol / calendars / debit spreads.
- **Risk-reversal at an extreme** => skew mean-reversion / risk-reversal trades.
- **Backwardation + rising wings + negative gamma** => defensive; long-vol / tail;
  bounces lower-confidence.
- *(Descriptive only; signals come from `strategies/`.)*

## 7. Writing the surface + flow report (house template)

Three parts, mirroring the reference report:

**1) The Read** - describe the surface in plain numbers:
- the crash bid (deep-OTM put IV across tenors + the hottest strike),
- the call wing (short-dated vs back-end IV),
- the ATM term structure (front -> back, contango/backwardation, with %),
- the put-skew steepness (risk reversal in vol pts, front vs back).

**2) The Flow** - the day's option flow:
- notional balance (put $ vs call $, # of prints, underlying range),
- the 2-3 **largest structures**: legs, strikes, IV, and *what each expresses* -
  deep-ITM calls = synthetic long delta; an ATM straddle = a clean directional-
  agnostic vol bet; a far-OTM put wing = cheap tail insurance,
- accumulation patterns (repeated equal-size slices = a sweep worked across venues).

**3) Speculation vs Hedging** - classify the two books running on the same tape:
- **Speculation** is *selective and structure-aware* - concentrated in ITM/near-ATM
  strikes where IV is cheap and delta does the work; little far-OTM upside.
- **Hedging** is *broad and programmatic* - multi-tenor OTM puts paying the steeper
  front-month put IV, distributed across strikes and months.
- Use the notional tilt (e.g. "1.7x toward puts => defensive session") but judge the
  *structure*: layered, multi-tenor, distributed = ongoing portfolio management, not
  a directional bearish bet.

---

## Sources
- `research/`: Riding on a Smile; Santander Volatility Trading Primer (Part I);
  Trading Volatility; Forecasting Implied Volatility Surface Dynamics; the charm
  ("Cracking the Code on Charm") and 0DTE delta-hedging notes.
- IV-HV / IV-Rank screening method: Market Chameleon, Barchart, Optionistics
  (implied-vs-historical-volatility screeners).
- House surface + flow report style (reference report provided by Mithil).
