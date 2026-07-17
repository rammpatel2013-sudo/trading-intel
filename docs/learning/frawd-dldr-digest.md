# FRAWD Research / DLDR — methodology digest & replication notes

*Compiled 2026-07-15. Purpose: capture what the FRAWD Research LETF net-issuance models
disclose **publicly and for free**, and scope what it would take to replicate the DLDR
signal inside `trading-intel`. This is a knowledge note, not an endorsement of the "fraud"
thesis — see "Credibility / caveats".*

---

## TL;DR

- **The methodology is fully public and free.** You do **not** need a FRAWD subscription to
  learn or replicate the signal. The author's own DLDR abstract states: *"All methodology is
  fully disclosed to support independent validation, replication, and legal inquiry."*
- The subscription ($9.99/mo → $12.99/wk tiers) buys **live dashboards, real-time portfolio,
  "today's trades," and backtest views** — packaging and daily calls, not data or method.
- **Nothing DLDR-related exists in this repo yet** (grep for issuance/DLDR/shares_outstanding/
  creation-redemption is clean). This would be **net-new**, not a diff against prior work.
  Our system is an options/Greeks stack; ETF creation/redemption flow is a new data domain.
- The core input — **daily change in LETF shares outstanding** — is published daily by the
  sponsors (Direxion, ProShares, etc.) and is reachable via our existing **FMP** key
  (`clients/fmp.py`, though the specific shares-outstanding method isn't implemented yet).
- **The flow/input layer is now fully specced** from the uploaded "Net Issuance Flows (Full
  Version)" dashboard (`docs/learning/LETF Net Issuance Flows (Full Version) _ FRAWD.pdf`) —
  see "Net Issuance Flow dashboard — exact spec". Its internal arithmetic was verified and is
  self-consistent.
- **The paywalled DLDR backtester and live Portfolio were captured via Claude in Chrome (2026-07-15)**
  — see "Live dashboard captures". DLDR/SQQQ shows +680% (vs avg position) over 2016–2026; the live
  short-LETF book shows +26.62% vs QQQ +24.77% since 2025-09-04 at beta 0.41. Sizing = a linear
  **Scale** factor, execution = day-late ProShares NAV, FIFO. Little of substance is left behind the
  paywall.

---

## Who / where (all free)

| Asset | What it is | Link | Access |
|---|---|---|---|
| DLDR paper — "Am I the Patsy? LETF Issuance is Signal, Not Noise" | The flow strategy (16 pp) | SSRN 5360727 | Free download |
| "Deception by Design: Leveraged ETFs, Structural Fraud, and the Proof of Outperformance" | The "$100B net-issuance" math proof underpinning DLDR | SSRN 5347238 | Free download |
| *Structured Fraud* (book) | Man-in-the-Mirror / Double-Down methodology narrative | Amazon Kindle (B0DJGC9H8M) | Paid (cheap) |
| FRAWD Research site | Live dashboards + model one-liners | frawdresearch.com | Model **rules** shown free; **dashboards/portfolio** paywalled |
| Author | Rob Bezdjian — independent researcher, SEC whistleblower (first LETF TCR filed Dec 17, 2019) | SSRN AbsByAuth per_id 5957633 | — |

FRAWD's own homepage routes every **"Methodology"** link to the two SSRN papers or the book —
i.e. the vendor itself points you at the free sources for the method.

---

## Disclosed rule set

### DLDR ("Day Late Dollar Richer") — the flow model
Verbatim/near-verbatim from the SSRN abstract + the FRAWD model card:

- **Signal:** daily change in **shares outstanding** of an LETF (Δ shares = net creation/redemption).
- **Direction mapping:** **issuance → short; redemption → buy.** (Fund grew shares = go short next
  session; fund shrank shares = go long.)
- **Timing:** act with a **one-day lag** ("a day late") — you observe today's EOD share count,
  trade the next session.
- **Sizing (as stated):** *"trade 1/100,000th of the share delta."* (Exact normalization /
  capital scaling is in the PDF body — see gap.)
- **Execution / accounting in backtest:** **FIFO covers**, **NAV execution** (fills modeled at NAV).
- **Live tracked instrument:** **$SQQQ** on the public DLDR dashboard.
- **Universe in the paper:** a "representative subset" of LETFs; the full dataset + backtest went
  to the SEC under whistleblower protection.
- **Thesis:** LETFs are "functionally zero-sum"; issuance behavior is a *structural* predictive
  signal (distinct from the classic volatility-decay/compounding story).

### Man in the Mirror / Double Down — the pair models (SOXL/SOXS, JNUG/JDST, etc.)
From the FRAWD model card (full method = the book):

- **Structure:** long/short **pairs** — e.g. SOXL/SOXS, GDXU/GDXD, NUGT/DUST (JNUG/JDST is the
  gold-miner pair analog).
- **Rebalance:** **quarterly**, with a **"top-up" rule.**
- **Objective:** **high Sharpe, low beta.**
- There's also a standalone Patreon post: *"How I trade JNUG/JDST for a 10% average gain each quarter."*

### FRAWD Model Portfolio — the live book
- Signals → trades → cumulative P&L; **14–24 LETFs**; "real pricing + tracking";
  "independent replication encouraged (paper trade)."

---

## Net Issuance Flow dashboard — exact spec

*Source: uploaded PDF `docs/learning/LETF Net Issuance Flows (Full Version) _ FRAWD.pdf`
(the paywalled "Full Version" page, captured 2026-07-15). This is the **flow/input layer** that
feeds DLDR — not the trading backtest. Tagline on the page: "Reads daily shares outstanding
changes across leveraged & inverse ETFs and summarizes net creations/redemptions versus AUM
change." All aggregate arithmetic below was recomputed and is internally consistent.*

**Per-ticker table columns (this is the whole engine):**
`Ticker | Shares (outstanding) | NAV Close | Δ Shares (latest day) | $ Flow (latest) | Cum Δ Shares (range)`
with `IN = green` (creation), `OUT = red` (redemption).

Worked rows from the capture:

| Ticker | Shares | NAV Close | Δ Shares (latest) | $ Flow (latest) | Cum Δ Shares (range) |
|---|---|---|---|---|---|
| SOXL | 135,450,060 | $176.66 | 0 | $0 | 8,750,000 |
| TQQQ | 483,900,000 | $75.05 | 350,000 | $25,437,230 | 8,150,000 |
| SPXL | 25,300,001 | $275.63 | 0 | $0 | 1,550,000 |
| SQQQ | 51,601,200 | $38.62 | 2,150,000 | $85,837,245 | 4,100,000 |
| LABU | 2,324,486 | $274.05 | 0 | $0 | -700,000 |

**Aggregates (range 2026-06-15 → 2026-07-15, 20 trading days, 49 tickers loaded):**

- Start AUM $118,154,960,463 → End AUM $100,577,530,014 → **AUM Δ −$17,577,430,449**
- **Net Issuance Flow (range) $2,076,377,540** (avg/day $103,818,877)
- **Net Δ shares (range) 18,440,000** (avg/day 922,000); latest-day Δ shares −2,955,000
- **Issuer buckets:** ProShares IN $1,030,623,440 · Direxion IN $1,023,008,500 · Defiance IN
  $22,745,600 — these **sum exactly to Net Issuance Flow** (verified).
- **"Show Me the Money Clock"** = **AUM Δ − Net Issuance Flow** = −$19,653,807,989
  (green = "patsy won", red = "Structured Fraud won"). Formula verified.
- Market returns (range): SPY −0.4%, QQQ −3.27%, "Patsy" −16.63%.
- Universe seen in the capture (subset of 49): SOXL, SQQQ, TSLL, TQQQ, TMF, UVXY, SBIT, METU,
  SPXU, FAS, AGQ, SPXL, BOIL, LABU, TNA, TECL.

**Replication-critical nuance — the `$ Flow` price is NOT the displayed NAV Close.**
Back-solving `$ Flow ÷ Δ Shares`: SQQQ → **$39.92** (vs NAV Close $38.62); TQQQ → **$72.68**
(vs $75.05). So `$ Flow` is priced off a *different* value than the NAV column shown — most
likely prior-day NAV or the creation-unit price. Nail this down from the SSRN DLDR paper before
trusting any `$ flow` we compute, or our numbers will drift from theirs by ~1–3%.

**Bottom line:** this entire dashboard is `Δ(shares_outstanding) × price` plus roll-ups. With
FMP shares-outstanding + NAV/close we can reproduce it end-to-end; there is nothing proprietary
in the flow layer itself.

---

## Live dashboard captures (Claude in Chrome, 2026-07-15)

*Pulled directly from the logged-in (samkumar) FRAWD session. These are the paywalled dashboards.
The DLDR page is an interactive backtester; the Portfolio page is a live book.*

### DLDR Dashboard — backtest, SQQQ
Subtitle confirms the engine: **"Gain/Loss (PnL) curve • Day-late execution using ProShares NAV +
Shares Outstanding."** Inputs: Ticker **SQQQ**, **Scale 1/100,000**, window **2016-07-14 →
2026-07-14**; underlying data **2010-08-30 → 2026-07-14 (3,991 rows)**.

| Metric | Value | Definition shown |
|---|---|---|
| Liquidation / Total P&L | **$133,617.85** | Realized + Unrealized |
| Realized P&L | $132,219.64 | FIFO lots closed |
| Unrealized P&L | $1,398.20 | Open position marked to end price |
| Max Loss (only below $0) | **−$79.27** | Ignores giving back gains |
| Peak Value (Peak P&L) | $135,492.37 | Highest P&L reached |
| % Return (Total P&L / Avg Position Size) | **679.80%** | Total $133,617.85 / Avg Pos $19,655.48 |
| Peak Pocket Used (True Out-of-Pocket) | $37,799.44 | Offset model: max(0, max(LongMV, ShortMV) − P&L) |
| % Return (Total P&L / Peak Pocket Used) | **353.49%** | Total $133,617.85 / Peak Pocket $37,799.44 |
| Avg Position Size (Abs Net) | $19,655.48 | Avg \|netShares\| × price |
| Ending # Shares (rounded) | −494 | Runs fractional behind the scenes |
| Ending Share Price / Net Value | $38.62 / −$19,096.82 | — |

PnL curve: Min −$50,000 · Max $150,000 · **Last $133,617.85**, steadily rising (esp. post-2020).

**This closes most of the old gap:** "1/100,000th of share delta" is a literal **Scale** dropdown;
execution = **day-late on ProShares NAV**; **FIFO** confirmed; return is reported two ways (vs avg
position size, and vs peak out-of-pocket capital). Sizing is a linear scale factor, so the *%
returns* (680% / 353%) are the scale-invariant figures that matter; the dollar figures scale with it.
Note the tiny **Max Loss (−$79)** vs Peak Pocket ($37.8k) — the model claims to almost never sit
underwater. (Single ticker captured; the native ticker dropdown resisted automation, but SQQQ is the
instrument FRAWD itself live-tracks.)

### Frawd Portfolio (Real-Time + Scaled) — the live book
Header: **As-of 2026-07-15 · Baseline 2025-09-04 · 730 txns · Scale $5,000.** A live web app; all
15 positions are **short** LETFs (the "short-the-LETF" strategy Rob submitted to the SEC).

Performance since the 2025-09-04 baseline (~10 months):

| | Value |
|---|---|
| **FRAWD vs SPY vs QQQ** | **+26.62%** vs SPY **+16.28%** vs QQQ **+24.77%** |
| Alpha (annual) | **+27.44%** |
| Beta (vs SPY) | **0.41** |
| Max Drawdown | **−7.96%** |
| Current portfolio borrow rate | 4.70% |
| Total Portfolio Value | $6,331.23 |
| Cash Balance | $200.33 |
| Total Realized P/L | **$1,768.77** |
| Total Unrealized P/L | **−$199.20** |

Top positions (9 of 15 captured; Symbol · Qty · Price · MktVal · %Port · Unrlzd$ · Unrlzd%):
METU −5 $29.00 −$145.00 2.29% −$46.05 **−46.54%** · SSPC −10 $15.53 −$155.30 · AAPB −4 $43.32
−$173.28 +0.14% · TSLL −15 $12.22 −$183.30 **+11.53%** · TQQQ −3 $74.44 −$223.32 −0.70% · TSDD −30
$7.65 −$229.50 · SBIT −5 $55.72 −$278.60 +9.65% · SCO −10 $28.29 −$282.90 · YANG −10 $31.82 −$318.20
**−22.25%**. Collapsible sections on the page: Open Lots (FIFO), Closed Lots (FIFO), Cash Events,
Borrow Rates (Current Shorts).

**Read on it (evenhanded):** risk-adjusted it looks strong — beating QQQ while running beta 0.41 and
a −7.96% max DD gives the +27.44% alpha. But the *raw* edge over this specific window is thin
(+26.62% vs QQQ +24.77%, ~1.9 pts), current unrealized P&L is negative, and individual shorts carry
real squeeze risk (METU −46.5%, YANG −22.3% unrealized). It's a tiny book (~$6k, scale $5,000) and a
short-only sleeve — treat the headline as a strategy sleeve, not a standalone track record. Borrow
cost (4.70%) is a real drag the backtest's "NAV execution" may understate.

---

## GAP — what's still behind the PDF (needs a local copy)

The flow layer AND the DLDR backtest engine are now both captured (see dashboard captures above).
Most of the original gap is closed:

- [x] **Position-sizing normalization** — it's a literal **Scale** dropdown (e.g. 1/100,000); a
  linear factor, so the **% returns are scale-invariant** (680% vs avg position, 353% vs peak pocket).
- [x] **Execution / accounting** — day-late on **ProShares NAV**, **FIFO** lots, offset/peak-pocket
  capital model = `max(0, max(LongMV, ShortMV) − P&L)`.
- [x] **Backtest metrics** — captured live for SQQQ + portfolio-level Alpha/Beta/MaxDD.
- [~] **Universe** — partially known (flow: 49 LETFs; portfolio: 15 short names incl. METU, SSPC,
  AAPB, TSLL, TQQQ, TSDD, SBIT, SCO, YANG).

Still genuinely open (small):

- [ ] Exact **entry/exit thresholds** / whether there's a dead-band, or whether it trades every day
  proportional to sign(Δshares). Dashboard implies daily; paper would confirm.
- [ ] The flow dashboard's **`$ Flow` price basis** fine detail (≠ displayed NAV close — see nuance).
- [ ] **"Deception by Design"** derivation of the **$100B** figure (SSRN 5347238 body).
- [ ] **Man in the Mirror & Double Down** model specifics + live numbers (slugs renamed; not yet pulled).
- [ ] Full **15-position** portfolio list (6 rows were below an un-scrollable iframe fold) + per-ticker
  DLDR runs beyond SQQQ (native ticker dropdown resisted automation).

**To close the last items:** either I re-run Claude in Chrome to grab Man-in-the-Mirror / Double Down
and scroll the remaining rows, or download SSRN 5347238 ("Download This Paper", free) into
`docs/learning/` for the $100B derivation.

---

## Replication scope inside `trading-intel`

Net-new domain (ETF creation/redemption ≠ options Greeks). Fits our architecture as follows:

**Data (rule 1 — no vendor calls outside `clients/`):**
- Add a shares-outstanding method to `clients/fmp.py` (FMP has historical shares / ETF endpoints;
  our key already covers the 157-endpoint tier — the method just isn't written yet). Sponsor sites
  (Direxion/ProShares) are the primary source of truth if FMP lags.
- `clients/prices.py` already covers NAV/close for execution modeling.
- Define an `EtfFlowSource` Protocol in `clients/__init__.py` (mirror of the `OptionsDataSource`
  pattern) so downstream code consumes flows through the abstraction.

**Persistence (rule 3):** Alembic migration for a `letf_shares_snapshots` table
(`ticker, date, shares_outstanding, delta_shares, nav`), idempotent `INSERT ... ON CONFLICT`.

**Signal (rule 4 — only `strategies/` writes to `signals`):** a `strategies/dldr.py` implementing
the `SignalGenerator` Protocol. This is the *sanctioned* place for it — DLDR is a validated-style
scanner, not a raw Greek crossing, so it's allowed to emit signals. Document logic in
`docs/playbooks/dldr.md`.

**Job (rule 5):** `scheduler/jobs/letf_flows.py` with idempotent `run(session, source)`, EOD cron;
on the NAS this needs a DSM task (see MEMORY `### NAS deployment`), not just `runner.py`.

**Validation before trusting it:** re-derive the backtest on our own share-count history and compare
Sharpe/CAGR to the paper's tables (once we have them). Treat the "consistent profit" claim as
*unverified* until independently reproduced — the abstract is self-reported and not peer-reviewed.

---

## Credibility / caveats (evenhanded)

- **Legit as open research:** real author, real DOIs, an actual SEC whistleblower filing, and a
  standing invitation to replicate. The *existence* of an issuance signal is plausible and testable.
- **Contested framing:** the "structural fraud" characterization is the author's interpretation, not
  established consensus. Mainstream finance attributes LETF underperformance to volatility
  decay/compounding, not deliberate issuance fraud.
- **Strong vs. weak claim:** "issuance is *arbitrageable* for consistent profit" is a much stronger
  claim than "issuance exists as a signal," and only the weaker one is well-supported publicly. Any
  live edge can decay once crowded.
- **Not financial advice.** This note is for building/validating the signal, not a recommendation to
  trade LETFs or subscribe.

---

## Lessons for trading-intel — what to actually adopt

*The FRAWD headline trade is not the prize. The prize is a free daily flow dataset, two mechanical
flow effects that plug straight into the gamma work, and some backtest hygiene worth stealing —
all kept in descriptor-not-signal discipline (rule 4).*

### 1. The one idea that fits us: LETF rebalance flow × dealer gamma
- A k× daily-reset LETF must rebalance every day to hold constant leverage. If the underlying moves
  `r` on the day, its required EOD trade in the underlying ≈ **k(k−1) · (fund net assets) · r**.
- `k(k−1) > 0` for **every** leveraged/inverse fund (k=3→6; k=−3→12; k=−1→2), so they **all trade in
  the direction of the day's move** — buy into strength, sell into weakness, near the close (inverse
  funds by covering). Mechanical, predictable, momentum-amplifying MOC flow.
- We already model **dealer options gamma (GEX)** as an EOD stabilizing/destabilizing force. LETF
  rebalance flow is a *second* mechanical EOD flow on the **same underlyings**. Fusing them answers
  "who is forced to trade into the close, and which way" — biggest where LETF assets are large vs ADV:
  **semis (SOXL/SOXS), single-stock LETFs (TSLL/TSLQ, NVDL/NVD), QQQ (TQQQ/SQQQ), SPX (SPXL/SPXU)**.
- Net complex rebalance = `Σ kᵢ(kᵢ−1)·Aᵢ·r` (all coefficients positive → additive into the move).
  **Shares-outstanding data keeps the Aᵢ term current** — exactly the free feed the FRAWD flow
  dashboard runs on. Clean tie-in: their input → our rebalance-flow estimate → sits beside GEX/DEX.

### 2. Data + descriptors to add (rule-compliant, ~free)
Already scoped above (FMP shares-outstanding → `letf_shares_snapshots` → EOD job). On top of raw flow:
- **Net issuance $ flow / cum Δshares / issuer buckets** — the FRAWD "Show Me the Money" view, trivially replicable.
- **Estimated daily rebalance notional** per concentrated underlying (the `k(k−1)·A·r` formula), as a
  **% of underlying ADV** → a "how much forced MOC flow" gauge.
- **Decay / roll drag** per LETF (realized vs k× underlying) — the actual, uncontroversial LETF edge.
- Route all of it into the dashboard + AM summary as **descriptors**, not alerts (rule 4).

### 3. Backtest / reporting techniques worth stealing
- **Peak-Pocket return** — %return vs *peak capital actually deployed* `max(0, max(LongMV,ShortMV) − P&L)`,
  not notional. Better denominator for Track A/B spread backtests where capital-at-risk ≠ notional.
- **Dual %return reporting** (vs avg position size AND vs peak capital) — honest bracketing.
- **Model borrow + slippage explicitly** — FRAWD's "NAV execution" flatters a short book; borrow on
  small LETFs (YANG/METU) runs 4–5 %+ and they're often hard-to-borrow. Bake into any short backtest.
- **Baseline-anchored live scorecard** — their FRAWD-vs-SPY-vs-QQQ + alpha/beta/maxDD chips are a clean
  pattern; add rolling beta-to-SPY, alpha, and max-DD to our flow scorecard / signal tracker.

### 4. What NOT to adopt
- **Don't trade DLDR on faith** — self-reported, short-only, ~$6k book, raw edge over QQQ ~1.9 pts,
  borrow unmodeled, unrealized P&L currently negative. Defensible core = *decay harvesting*; the
  *issuance-timing overlay* is the unproven part.
- **We can't run the short leg cleanly anyway** — a basket of small short LETFs needs reliable borrow +
  execution we've chosen not to automate ([[no-ibkr-api]]). Value to us = the **flow/descriptor layer**,
  not the trade.
- Stay inside FlashAlpha discipline: issuance is a regime **descriptor** like GEX/DEX, not an alert source.

### 5. Prioritized next steps
- **Small (first):** build the `letf_shares_snapshots` data layer (FMP method + `EtfFlowSource`
  Protocol + Alembic migration + idempotent EOD job + NAS DSM task). Unlocks everything else.
- **Medium:** add rebalance-notional + decay descriptors on the watchlist/AM summary beside GEX/DEX;
  add peak-pocket + borrow modeling to the backtest harness.
- **Large (research bet):** the LETF-rebalance × dealer-gamma EOD flow model for concentrated names —
  test whether combined forced flow predicts close→open drift, and *only if it clears* promote to a
  Track-B-style scanner signal.

**Build status (2026-07-15):** the "Small" data layer is written & validated — `EtfFlowSource`
Protocol + `SharesSnapshot` DTO (`clients/__init__.py`), `FmpClient.shares_outstanding`
(shares-float → /quote fallback; `clients/fmp.py`), `LetfSharesSnapshot` model, migration
`0032_letf_shares_snapshots`, config `LETF_SYMBOLS` (24 LETFs) + `letf_symbols`, and the idempotent
EOD job `scheduler/jobs/letf_flows.py`, **registered in `scheduler/runner.py` @ 17:10 ET Mon–Fri**,
with tests (`tests/scheduler/test_letf_flows.py` + `shares_outstanding` cases in
`tests/clients/test_fmp.py`). Wiring left: `alembic upgrade head` on the DB; add the NAS DSM task
(the runner cron is ignored on the NAS); then build the Δshares / rebalance-notional / decay
descriptors that read the banked table and surface beside GEX/DEX.

Relates to [[swing-trade-system-build]], [[convexvalue-extra-endpoints]] (und-vflowratio flow scanner),
[[index-etf-gex-dex-only]] (LETF→underlying mapping), [[no-ibkr-api]] (borrow/execution limits).

---

## Sources
- DLDR paper — SSRN 5360727: https://ssrn.com/abstract=5360727
- Deception by Design — SSRN 5347238: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5347238
- FRAWD Research: https://www.frawdresearch.com/
- Net Issuance Flows (Full Version) dashboard — captured PDF: `docs/learning/LETF Net Issuance Flows (Full Version) _ FRAWD.pdf` (source: https://www.frawdresearch.com/letfflowsfull)
- Book (Structured Fraud): https://www.amazon.com/dp/B0DJGC9H8M
- Author page (SSRN): https://papers.ssrn.com/sol3/cf_dev/AbsByAuth.cfm?per_id=5957633
