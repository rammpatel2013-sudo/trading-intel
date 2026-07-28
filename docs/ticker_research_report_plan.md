# Enhanced ticker research report — plan (2026-07-19)

Fuse the options/vol dashboard we already build (`scripts/ticker_report.py` /
`reports/_dashboard_template.html`) with **fundamentals + earnings transcript +
institutional holdings + investor-letter commentary** into one per-ticker research
one-pager, and add a **weekly digest summary**. Everything below sources from data we
already bank or can pull from **CVForge FMP** (no new vendor, ADR-004). Ties to your
stock-analysis framework (10x / SBC drag / debt vs cash / verdict).

## Two deliverables

1. **Enhanced per-ticker report** — extend the existing dashboard with new panels.
2. **Weekly digest summary** — what the letters + 13F pipeline surfaced this week.

---

## Suggested datapoints for the per-ticker report

Grouped by panel, with source and whether we already have it.

### 1. Snapshot
Price, market cap, sector/industry, ADV, **float**, **short interest % + days-to-cover**.
*Source: FMP profile/quote. Status: partial.*

### 2. Fundamentals — value + quality (drives the 10x / SBC / debt view)
- **Valuation:** P/E (ttm + fwd), EV/EBITDA, EV/Sales, P/FCF, **FCF yield**, PEG.
- **Growth:** revenue YoY + 3-yr CAGR, EPS growth, FCF growth.
- **Quality:** gross / operating / FCF margin, **ROIC**, ROE.
- **Balance sheet:** **net cash / (debt)**, net debt / EBITDA, **interest coverage
  (GAAP)**, current ratio.  ← your "debt vs cash" pillar
- **SBC drag:** SBC $, **% of revenue**, **% of market cap**, **share-count growth
  (dilution)**, adjusted-vs-GAAP gap.  ← your "SBC drag" pillar
- **Capital return:** buyback yield, dividend yield, total shareholder yield.
- **Estimates:** consensus rev/EPS next FY + **revision trend**.
*Source: FMP income/balance/cash-flow/ratios/key-metrics/estimates. Status: the factor
layer already banks some `fundamentals_snapshots`; add valuation / FCF / SBC / margin fields.*

### 3. Earnings & catalysts
- Next earnings date + BMO/AMC. *(have: `earnings_calendar`)*
- Expected move / pre-earnings straddle. *(have: `pre_earnings_straddle`)*
- **Last transcript:** tone, QoQ Δtone, guidance cues, 2–3 key quotes. *(have: earnings
  inflection detector + transcripts via FMP)*
- Earnings surprise history (beat/miss, last 4–8 q). *(FMP earnings-surprises)*

### 4. Positioning / options-vol  *(the existing dashboard — reuse as-is)*
GEX/DEX + gamma flip, call/put walls, IV rank, IV/RV (VRP), 25Δ skew, term structure,
options flow (net delta + notable prints), technicals (trend, key levels, AVWAPs).

### 5. Institutional & smart-money  *(the new fusion — 13F + letters)*
- **Institutional ownership % + QoQ Δ.** *(sentiment_snapshots / 13F)*
- **Tracked funds holding it** + new / added / trimmed / exited this quarter. *(our 13F
  pipeline — `filing_holdings`)*
- **Investor-letter commentary** — the actual quote / rationale from letters that mention
  the ticker, with the fund + date. *(our letters pipeline — `watchlist_entries` rationale
  + a pulled excerpt from the saved letter)*  ← "commentary by letter for that stock"
- Analyst rating + price-target consensus, upside to PT. *(sentiment_snapshots / FMP)*
- Insider (Form 4) net buy/sell. *(FMP insider-trading — optional)*

### 6. Verdict  *(your framework — LLM narrative)*
10x read, SBC-drag call, debt/cash call, overall verdict — a short local-Ollama synthesis
that ties vol + fundamentals + smart-money together (ResearchNote style, rule 7).

---

## Weekly digest summary report

- **New letters digested** (fund, date, one-line thesis).
- **New tickers** added to the research watchlist (+ which fund/letter).
- **Notable 13F moves** (new / added / exited across the tracked funds).
- **Cross-ref:** which surfaced names have upcoming earnings, or are already in your
  options watchlist / have an options-vol dashboard.

---

## Ready vs. needs building

- **Ready:** options/vol dashboard, earnings calendar + straddle + transcript inflection,
  13F holdings, letters→watchlist, research-note LLM, HTML report engine.
- **Small builds:** (a) pull the extra fundamentals fields (valuation / FCF / SBC / margins
  / estimates) from FMP into `fundamentals_snapshots`; (b) a per-ticker **letter-commentary
  linker** (`watchlist_entries` → the saved letter → the paragraph that names the ticker);
  (c) the **report assembler** (add panels 1–2–3–5–6 to the dashboard HTML); (d) the
  **digest summary** report.

## Proposed build order

1. **Digest summary** (fast — data already exists; pull a few real letters to seed it).
2. **Fundamentals panel** (FMP fields → `fundamentals_snapshots` → panel).
3. **Institutional + letter-commentary panel** on the ticker report.
4. **Verdict synthesis** (LLM) tying it together.

*Open: which panels are must-have for v1, and what to build first — see the questions.*
