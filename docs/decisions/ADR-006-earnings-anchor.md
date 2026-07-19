# ADR-006 — Earnings-date anchor + pre-earnings straddle baseline

**Status:** Accepted (2026-07-18)
**Related:** ADR-004 (CVForge secondary source), `docs/em-break-system-plan.md`, MEMORY `em-break-gamma-burnoff`, `convexvalue-extra-endpoints`

## Context

The EM-break / gamma-burn-off system needs to know (a) *when* a name reports earnings and (b) what the options-implied expected move was *just before* the print. Neither existed as a persisted concept. `earnings_events` was declared in `models.py` (migration 0001) but never populated, and `clients/convex_app.py::earnings_calendar()` wrapped the ConvexValue `earn_cal` endpoint but was unwired.

Without the anchor there is no way to label a name "post-earnings", no baseline to measure a break against, and the whole pattern is undetectable.

## Decision

1. **Adopt `earnings_events` as the earnings-date anchor** rather than a new table. Add a `(symbol, date)` unique constraint (migration 0037) for idempotent upserts. The table already carried `time` (BMO/AMC) and read-through fields — a good fit.
2. **Add `pre_earnings_straddle`** (migration 0037): one row per `(symbol, earnings_date)` holding the pre-print ATM straddle + `em_pct = straddle/spot`, upserted daily within `PRE_EARNINGS_SNAP_DAYS` of the event so it always holds the freshest pre-print read.
3. **Expose earnings dates through a typed Protocol**, not a raw dict. `EarningsCalendarSource` + `EarningsDate` (`clients/__init__.py`), implemented by `ConvexAppClient.upcoming_earnings()`, keeping the vendor JSON shaping at the client boundary (rule 1). Parsing lives in a pure `clients/earnings_parse.py` so it is unit-tested without HTTP.
4. **No new vendor.** `earn_cal` is the existing ConvexValue pro login. Per rule 1's spirit this still warrants an ADR because it introduces a new persisted data concept + a new Protocol.

## Consequences

- The pattern becomes detectable and the pre-earnings baseline banks forward (it cannot be reconstructed for past prints — chain history is thin).
- The exact `earn_cal` column names / date encoding were **not** probed live; the parser is defensive and must be confirmed against a live pull (documented in the deploy plan). This is the one live-data unknown, analogous to the factor-scoring FMP field-name confirmation.
- `earnings_events` is now written by `scheduler/jobs/earnings_calendar.py`; the read-through fields (`actual`, `surprise_pct`, `peer_impacts`) remain unused and available for a future earnings-reaction feature.

## Alternatives considered

- *A dedicated `earnings_dates` table* — rejected; `earnings_events` already modelled exactly this.
- *Consume the raw `earn_cal` dict downstream* — rejected; violates the typed-Protocol boundary and spreads vendor-shape knowledge.
