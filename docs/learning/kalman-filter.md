# Kalman Filter — Reference & Implementation Notes

> Source: Phosphen (@phosphenq) X article "How Hedge Funds Use The Kalman Filter To Read Hidden Markets In Real Time" — May 18, 2026. https://x.com/phosphenq/status/2056438562451513660
>
> Captured into trading-intel docs because Kalman is directly applicable to **vol tracking, dynamic beta, GEX:RVOL ratio, and Thrasher signal recalibration** — all in Phase 5 of MASTER_PLAN.md.

---

## The core insight (one paragraph)

Most traders treat prices, realized vol, and rolling betas as *truth*. They are actually *noisy measurements* of unobservable, time-varying states. OLS, EWMA, GARCH, and rolling-window stats all conflate the variance of recent returns with the current variance of underlying state — which means they react too slowly in regime shifts and too fast in calm conditions. The Kalman Filter is the optimal solver for "estimate a hidden time-varying state from a stream of noisy observations in real time." It is **not** a predictor. It is a state estimator.

## When to reach for it

- You believe the thing you're tracking is a latent, slowly-drifting state (not a deterministic value).
- Your current implementation uses rolling windows (20-day RV, 60-day beta) and you've noticed it lags regime shifts.
- You can write down a process model (how the state evolves) and a measurement model (how observation relates to state).

When NOT to reach for it: when the process truly is constant (no point estimating a moving target that isn't moving) or when non-linearity dominates (need Extended Kalman Filter — EKF — instead).

---

## The math, in 6 equations

A Kalman Filter sits on top of a state-space model. Two equations define the model. Four equations define the recursive update loop.

### State-space model

**Process model** (how hidden state evolves):
```
x_t = F · x_{t-1} + w_t       where w_t ~ N(0, Q)
```
- `F` = state transition matrix
- `Q` = process noise covariance — how much the state can drift between steps

**Measurement model** (how observation is generated from state):
```
z_t = H · x_t + v_t           where v_t ~ N(0, R)
```
- `H` = measurement matrix
- `R` = measurement noise covariance — how noisy the observations are

Six matrices total: `F, Q, H, R, x₀, P₀`. Everything else falls out.

### Recursive update loop

**Predict** (project forward using process model, before observation):
```
x̂_{t|t-1} = F · x̂_{t-1|t-1}
P_{t|t-1} = F · P_{t-1|t-1} · Fᵀ + Q
```
Uncertainty grows because the state has drifted and no new information has arrived.

**Innovation, innovation covariance, Kalman gain**:
```
y_t = z_t - H · x̂_{t|t-1}                  # innovation (residual)
S_t = H · P_{t|t-1} · Hᵀ + R                # innovation covariance
K_t = P_{t|t-1} · Hᵀ · S_t⁻¹                # Kalman gain
```

**Update** (merge prior with new observation):
```
x̂_{t|t} = x̂_{t|t-1} + K_t · y_t
P_{t|t} = (I - K_t · H) · P_{t|t-1}
```
Uncertainty shrinks because new information has arrived.

### Why the Kalman gain matters

In scalar form:
```
K = P_prior / (P_prior + R)
```
Prior uncertainty divided by total uncertainty.
- When prior is much more certain than the observation → K → 0 → ignore observation.
- When observation is much more certain than the prior → K → 1 → trust the observation.
- Optimal interpolation — minimizes mean squared error of the posterior among all linear estimators.

This is the math that tells you exactly how much to trust new evidence relative to your prior.

---

## Three applications (in order of relevance to trading-intel)

### A. Dynamic beta — for pair trades and hedge sizing

**State-space model:**
```
β_t = β_{t-1} + w_t,    w_t ~ N(0, Q)          # state evolves as random walk
r_a,t = β_t · r_m,t + v_t,    v_t ~ N(0, R)    # measurement with time-varying H_t = r_m,t
```

- `Q` controls smoothness — smaller = smoother estimate, larger = more responsive.
- Output: the current β with an uncertainty band, not a single trailing-window number.

**Why this matters:** OLS gives you the average β over a window. Kalman gives you the *current* β with its uncertainty. For pair trading SPY/QQQ, the dynamic β oscillates within a meaningful range (article shows ~1.20 ±0.10 one-sigma). Trading on the OLS average when the current state is at the edge of the distribution = systematic error.

**Position sizing rule from the article:** compute z-score of current β vs trailing 1-year mean/std of the β series. Use the z-score as a directional signal for relative-value pair trades.

### B. Volatility tracking — alternative to GARCH/EWMA

**State-space model:**
```
log(σ²_t) = log(σ²_{t-1}) + w_t,    w_t ~ N(0, Q)    # state in log-space keeps σ² positive
log(r_t²) = log(σ²_t) + η_t                         # observation
```

For Gaussian returns the squared return is chi-squared distributed and η_t is not exactly Gaussian — but the linear Kalman approximation works in practice and is what production desks run.

**Why this matters:**
- EWMA/GARCH/rolling-stddev all treat the squared return as a direct observation of variance. It isn't — each daily squared return is itself a single noisy draw from the true variance distribution.
- Kalman vol gives you a smoother current-state estimate with a one-step-ahead projection.
- Drives **vol-targeting position sizing**: `position = target_vol / kalman_vol`, clipped by max leverage. Bridgewater's risk parity is built on a generalization of exactly this.

### C. Order book imbalance / noisy intraday signals

Order book depth swings on every cancellation and re-rest. The latent quantity — actual institutional pressure — is what you'd want to estimate. Kalman filters intraday flow data the same way it filters returns.

---

## Critical limitations (the author was honest)

Three assumptions determine whether Kalman survives contact with live markets:

1. **Linearity.** Standard Kalman assumes `F` and `H` are linear. Things like implied vol surface evolution or options price responses to underlying are non-linear → need **Extended Kalman Filter (EKF)** or **Unscented Kalman Filter (UKF)** which linearize around the current estimate.

2. **Gaussian noise.** Real return innovations are fat-tailed. Standard Kalman over-trusts large innovations. Fix: **robust Kalman filtering** with a Huber-style gain that downweights extreme innovations. Small efficiency loss in normal regimes, large benefit during crashes.

3. **Stationarity of Q and R.** Both are usually treated as constants. They aren't — innovation variance changes with regime, liquidity, time of day. **Production rule:** if observed innovation variance diverges materially from S_t, your Q/R are wrong and the filter will degrade silently. Tune before trading.

---

## Implementation skeleton (rewrite from scratch when needed)

```python
# Belongs at: trading_intel/greeks/kalman.py (Phase 5+)
from __future__ import annotations

import numpy as np


class KalmanFilter1D:
    """Scalar Kalman filter for tracking a single hidden state (e.g., log-variance, beta)."""

    def __init__(self, x0: float, P0: float, F: float, Q: float, H: float, R: float):
        self.x = x0        # state
        self.P = P0        # state variance
        self.F = F         # state transition
        self.Q = Q         # process noise variance
        self.H = H         # measurement matrix
        self.R = R         # measurement noise variance

    def predict(self) -> None:
        self.x = self.F * self.x
        self.P = self.F * self.P * self.F + self.Q

    def update(self, z: float) -> None:
        y = z - self.H * self.x                      # innovation
        S = self.H * self.P * self.H + self.R        # innovation covariance
        K = self.P * self.H / S                      # Kalman gain
        self.x = self.x + K * y
        self.P = (1 - K * self.H) * self.P

    def step(self, z: float) -> tuple[float, float]:
        self.predict()
        self.update(z)
        return self.x, self.P


# Multidimensional version uses numpy linalg.solve for K = P · H.T · inv(S).
# Tune Q and R per use case — see calibration notes below.
```

### Tuning Q and R (the actual hard part)

- Start with `R = sample variance of observation` over a calibration window.
- Start with `Q = (R / 10) ** 0.5` and iterate.
- Monitor `S_t` (predicted innovation variance) vs realized innovation variance over a rolling window. If realized >> predicted → increase Q. If predicted >> realized → decrease Q.
- For vol tracking specifically, the author's defaults were `q=0.1, r=1.0` — start there and adjust by visual inspection of the filtered series against VIX/realized vol.

---

## How this fits trading-intel — Phase by Phase

### Phase 1 (now): NOT applied.
Building the Convex pipeline. No state estimation work yet. Just need data flowing.

### Phase 4 (Strategy ports — week 6–8): Optional but high-leverage.
**Add Kalman dynamic beta to `strategies/jdintown.py` pair-trading sizing.** The JD Intown framework already evaluates relative strength across tickers. Replacing OLS regression with Kalman β gives:
- More responsive pair-trade entry timing
- Honest uncertainty bands on hedge ratios
- Position sizing that scales with how stable the relationship is

**Specific file/method to update later:** when porting JD's swing scanner, wherever beta or correlation enters position sizing, swap the rolling regression for a Kalman call.

### Phase 5 (AM Summary + Anomaly detection — week 8–10): Critical.
**This is where Kalman delivers its biggest value for trading-intel.**

Three concrete swaps to make in Phase 5:

1. **`strategies/thrasher.py` (VIX dispersion signal)** — currently the plan uses 20-day rolling stddev of VIX and VVIX. Replace with Kalman vol tracking on each. Cleaner regime detection, fewer false signals when VIX is recovering from a spike. Re-calibrate the 0.86 / 3.16 thresholds against Kalman-filtered series.

2. **`greeks/regime.py` (GEX:RVOL ratio)** — currently `GEX / 20-day RV` (where 20-day RV is the noisy estimate the article critiques). Replace the denominator with Kalman-estimated current realized vol. The regime classifier becomes more responsive without becoming jumpy.

3. **`synthesis/anomaly_detector.py` (Spot Up + Vol Up check)** — uses changes in vol. Define "vol up" as Kalman vol step being more than 2σ above prior Kalman vol, rather than a 1-day percent change. Catches genuine regime shifts; ignores intraday noise.

### Phase 6 (Earnings ripple — week 10–11): Medium relevance.
**`strategies/earnings_ripple.py`** — peer-impact correlations drift over time. Use Kalman-estimated dynamic correlations between (LULU ↔ NKE), (AMD ↔ NVDA), etc. so the read-through classification stays accurate as relationships evolve.

### Phase 7+ (Probability model): Foundational.
The probability layer that combines GEX + DEX + VEX + ATM IV + VIX + credit spreads (per FlashAlpha rule) is exactly the kind of multi-state, time-varying system Kalman was designed for. Build the layer with Kalman-estimated state inputs from the start.

---

## Specific use for swing trading vs long-term signal

**Swing trading (1–10 day holds) — primarily JD Intown + Fib setups:**
- **Dynamic β (Application A)** is the highest-leverage improvement. Pair-trade sizing, hedge ratios, and relative-value setups all benefit immediately.
- **Kalman vol (Application B)** gives faster regime classification at the open — useful for the "first 30-min vol classifies trend vs chop" rule in MASTER_PLAN.md.

**Long-term signal generation (the 5-condition confluence vol-spike model):**
- **All three applications** feed in. Thrasher signal becomes cleaner. GEX:RVOL ratio becomes more responsive. Spot Up + Vol Up anomaly becomes more reliable.
- The probability model in Phase 5+ is where the combined effect compounds.

---

## Action items for the build

- [x] Capture this learning note. (you're reading it)
- [ ] **Phase 4:** add a `from trading_intel.greeks.kalman import KalmanFilter1D` call in `strategies/jdintown.py` for dynamic β sizing.
- [ ] **Phase 5 task 1:** implement `trading_intel/greeks/kalman.py` with `KalmanFilter1D` + multidimensional version.
- [ ] **Phase 5 task 2:** use it in `strategies/thrasher.py` (VIX vol-of-vol regime).
- [ ] **Phase 5 task 3:** use it in `greeks/regime.py` (GEX:RVOL ratio denominator).
- [ ] **Phase 5 task 4:** use it in `synthesis/anomaly_detector.py` (Spot Up + Vol Up trigger).
- [ ] **Phase 5 task 5:** calibrate Q and R for each application against 2020–2025 backtest data.
- [ ] **Phase 6 task:** apply to earnings-ripple peer correlations.
- [ ] **Backlog:** read up on Extended Kalman Filter (EKF) for non-linear cases (vol-surface evolution). The article author hinted at a Part 2 covering this.

---

## What to ignore from the article for now

- The full `MarketKalmanFilter` class running both β and vol estimation simultaneously. Build it incrementally instead — one Kalman per state, then compose.
- The Sharpe 0.44 backtest from the article. Not a money-printer, just a state estimator. The Sharpe comes from how you USE the cleaner estimates in your strategy.
- The "drop your answer in the replies" engagement bait at the end.

---

*Document version: 1.0 — captured May 19, 2026*
*Update when: Phase 4 / 5 / 6 implementation starts referencing it, or when EKF/UKF research is added.*
