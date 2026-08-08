# Swing-dossier data wiring — apply guide

New files delivered into the repo (additive, nothing imports them yet, so `pytest` stays green):

- `scripts/probe_swing_endpoints.py` — verify which FMP endpoints work on your key.
- `trading_intel/clients/finra.py` — FINRA short-interest client (the one true gap).
- `trading_intel/earnings/kpi_extract.py` — transcript → NRR/cRPO/margin KPIs (local Ollama).
- `alembic/versions/0042_swing_kpi_shortint.py` — `kpi_snapshots` + `short_interest_snapshots` (chains off head `0041`).

Below are the edits to **existing** files — review + run `pytest -q` after each.

---

## 0. First: verify what your key actually serves

```bash
python -m scripts.probe_swing_endpoints NET --year 2026 --quarter 1
```

Reads the .env locally (key never leaves your machine). It tries both routes — direct free `FmpClient` and the CVForge `cv_live` passthrough — and prints `OK (n) / EMPTY / ERR 402|403|502` per endpoint. **Rule of thumb from the docs:** `analyst-estimates`, `income-statement`, `key-metrics`, `grades-historical`, `price-target-consensus` should work; `insider-trading/search` and `institutional-ownership/*` are FMP-premium and will likely 402/403 → route those through `edgartools` (you already have `clients/edgar.py`), not FMP. Wire the **winning spelling** the probe reports.

## 1. `clients/fmp.py` — add grades (revision breadth)

```python
    def grades_historical(self, ticker: str, *, limit: int = 20) -> list[dict]:
        """Historical analyst grade actions (upgrades/downgrades) — revision breadth.
        Stable ``/grades-historical``; each row has date/newGrade/previousGrade/
        gradingCompany/action. []-on-failure, descriptive (rule 4)."""
        data = self._get("grades-historical", symbol=ticker, limit=limit)
        return data if isinstance(data, list) else []

    def price_target_consensus(self, ticker: str) -> dict | None:
        """Consensus price target (targetHigh/Low/Median/Consensus)."""
        data = self._get("price-target-consensus", symbol=ticker)
        return data[0] if isinstance(data, list) and data else None
```

(If the probe shows a different working spelling, use it here.) Revision *breadth* = count `newGrade > previousGrade` vs `<` over the last 30/90d from `grades_historical`.

## 2. `memory/models.py` — two ORM models (mirror `EstimateSnapshot`)

```python
class KpiSnapshot(Base):
    __tablename__ = "kpi_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    period_label: Mapped[str] = mapped_column(String(16))   # "2026Q1"
    ts: Mapped[date] = mapped_column(Date)
    dbnrr_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    revenue_growth_yoy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    operating_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    crpo_growth_yoy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpo_growth_yoy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    customers_over_100k: Mapped[float | None] = mapped_column(Float, nullable=True)
    customers_over_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    fcf_margin_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    guidance_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    one_line_kpi_read: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    __table_args__ = (UniqueConstraint("symbol", "period_label", name="uq_kpi_symbol_period"),)


class ShortInterestSnapshot(Base):
    __tablename__ = "short_interest_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    ts: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(24))         # "regsho_daily" | "finra_si"
    short_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_volume_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_volume_ratio_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_interest: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_daily_volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    days_to_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("symbol", "ts", "source", name="uq_shortint_symbol_ts_source"),)
```

Then apply the migration: `alembic upgrade head` (verify `alembic heads` == `0042` first; round-trip test `alembic downgrade -1 && alembic upgrade head`).

## 3. `config.py` — optional FINRA creds (daily proxy works without them)

```python
    FINRA_CLIENT_ID: str = ""
    FINRA_CLIENT_SECRET: str = ""
```

`.env.template`: add `FINRA_CLIENT_ID=` / `FINRA_CLIENT_SECRET=` (free at gateway.finra.org — unlocks the settled bi-monthly SI% + days-to-cover; without them you still get the daily Reg SHO short-volume proxy).

## 4. Two scheduler jobs (new files, mirror `estimate_snapshots.py`)

**`scheduler/jobs/short_interest_snapshots.py`** — for each watchlist symbol: `FinraClient(client_id=…, client_secret=…).short_volume_avg(sym, lookback=10)` (+ `settled_short_interest(sym)` if creds) → upsert `ShortInterestSnapshot` on `(symbol, ts, source)`. DSM: **daily ~18:30 ET** (Reg SHO files post after close).

**`scheduler/jobs/kpi_snapshots.py`** — for each watchlist name with a recent report: pull the latest transcript (`CVForgeClient.fmp("earning-call-transcript", {symbol, year, quarter})`), `extract_kpis(sym, text, OllamaProvider(settings), model=settings.LLM_TAGGING_MODEL)` → upsert `KpiSnapshot` on `(symbol, period_label)`. Runs **on-demand / weekly**, or fold into the Sunday orchestrator so it only runs for that week's reporters.

Both: `on_conflict_do_update` (rule 5 idempotent), local Ollama only (rule 7), `bind(correlation_id=…)` logging.

## 5. The Sunday orchestrator

`scheduler/jobs/weekly_swing_dossiers.py` — universe = `settings.watchlist_symbols` ∩ **date-verified** `earn_cal` for the coming Mon–Fri (verify each date against the FMP earnings calendar — earn_cal mis-dated CVX/XOM/CL and duplicated TTWO). For each name → `scripts/swing_dossier.py <SYM>` → HTML + a ranked index → Telegram. DSM: **Sunday ~08:00 ET** (`run_job.sh weekly_swing_dossiers`).

## ADRs (`docs/decisions/`)

- **ADR-008 FINRA short interest** — new free public source (Reg SHO daily + optional FINRA API); rule-1 isolated in `clients/finra.py`.
- **ADR-009 SEC/edgartools for 13F + Form 4** — FMP premium-gates these; use the free keyless SEC path (`clients/edgar.py` extended) instead.

## Commit (explicit paths — never `git add -A`, CRLF churn)

```bash
git add scripts/probe_swing_endpoints.py trading_intel/clients/finra.py \
        trading_intel/earnings/kpi_extract.py alembic/versions/0042_swing_kpi_shortint.py \
        trading_intel/clients/fmp.py trading_intel/memory/models.py trading_intel/config.py \
        docs/swing-dossier-wiring.md
pytest -q && git commit -m "swing dossier: FINRA + transcript-KPI collectors, grades, 0042 tables"
```
