# ADR-005 — Multi-factor scoring layer (fundamentals via CVForge FMP)

- **Status:** Accepted
- **Date:** 2026-07-16
- **Supersedes / relates to:** ADR-004 (CVForge secondary data source)

## Context

The swing (Track A) and credit-income (Track B) systems rank names on
positioning, vol, and technicals but have no **fundamental / cross-sectional
factor** view. A standard Value / Quality / Growth / Momentum / Risk layer would
let us rank the universe on business quality and valuation alongside the options
signals, and eventually feed the Phase-5 probability model.

Two hard rules bound the design:

- **Rule 1 (data-source isolation)** and the MASTER_PLAN's fixed vendor set — a
  new fundamentals vendor is off the table without its own ADR.
- **Rule 7 (cost-aware / local)** — no cloud LLM in any scored or scheduled path.

Fundamentals are already reachable: CVForge exposes the 157 FMP `/stable`
endpoints through the keyed passthrough we adopted in ADR-004
(`CVForgeClient.fmp`). No new credential, no new vendor.

## Decision

Add a descriptive multi-factor layer sourced entirely from the **existing**
CVForge FMP passthrough:

1. **No new vendor.** Fundamentals come from `CVForgeClient.fmp("ratios-ttm" | …)`
   and momentum from `CVForgeClient.aggs` — both already behind the `clients/`
   boundary (rule 1 preserved). FMP field spellings vary, so
   `factors/fmp_map.py` maps tolerantly (first numeric hit wins; every metric
   optional).
2. **`factors/` compute is pure.** `factors/compute.py` turns per-name inputs into
   **cross-sectional z-scores** per factor and a weighted composite, oriented so
   higher = more attractive. Scores are universe-relative (a z-score is only
   meaningful vs peers) — matching the "percentiles bank forward, cross-sectional
   rank interim" approach used elsewhere (no-IBKR percentile note). No LLM,
   numpy only (rule 7).
3. **Bank it.** `fundamentals_snapshots` (migration 0033) stores the raw inputs +
   the factor z-scores + composite, one row per (symbol, week), idempotent upsert
   on (symbol, ts) (rule 5). A weekly `scheduler/jobs/factor_scores.py` writes it.
4. **Descriptive only (rule 4).** Factor scores are research descriptors — never a
   standalone signal or alert. They may later gate the swing generator or feed the
   probability model, but only through a validated `strategies/` path.

## Consequences

- **No new vendor / credential / cost.** Reuses the ADR-004 key and the `clients/`
  boundary; adding fundamentals did not touch rule 1's vendor set.
- **FMP field fragility.** Endpoint field names differ by tier; the tolerant
  mapping degrades to `None` (and the factor simply drops that metric) rather than
  failing. The candidate key lists must be confirmed against the live payloads on
  first run — flagged in `fmp_map` and the runbook.
- **Universe-relative, not absolute.** A name's factor score depends on the batch
  it's scored with; comparisons are only valid within one run's universe. Absolute
  history banks forward in `fundamentals_snapshots` for a future within-name view.
- **Weekly cadence.** Fundamentals move slowly; weekly keeps FMP calls modest and
  the momentum window (~3m/12m from daily closes) stable.

## Alternatives considered

- **Dedicated fundamentals vendor (e.g. direct FMP / Sharadar).** Rejected — a new
  vendor + credential, which the MASTER_PLAN and rule 1 forbid without cause; FMP
  via CVForge already covers it.
- **Store raw fundamentals only, compute in the dashboard.** Rejected — factor
  logic belongs in a tested `factors/` module, not scattered in report code.
- **LLM-scored factors.** Rejected — rule 7; the scored path stays local numpy.
