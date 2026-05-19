# CLAUDE.md — Rules for AI-assisted development

This file is the rulebook for any AI coding assistant (Claude, GPT, Cursor, Copilot) working on `trading-intel`. Read this before touching anything.

---

## Project at a glance

`trading-intel` is an institutional-grade stock research system. It ingests options data from ConvexValue, computes regime descriptors (GEX/DEX/VEX/CHEX), runs strategy scanners, detects anomalies, and generates a daily 7 AM AM-summary via Claude API.

**Owner:** Mithil (drmithil@gmail.com)
**Primary data source:** ConvexValue (pro tier)
**Stack:** Python 3.11+, PostgreSQL 16 + pgvector, Streamlit, APScheduler, Anthropic API, Voyage embeddings
**Hosting:** Local dev → Digital Ocean droplet (Phase 7)

---

## Hard rules — never break these

### 1. Data source isolation
- **No direct `convexlib` or `schwabdev` calls outside `trading_intel/clients/`.**
- All downstream code consumes data through the `OptionsDataSource` Protocol (`trading_intel/clients/__init__.py`).
- If you need a new field from Convex, add it to the Protocol + implement in `convex.py` — never reach around the abstraction.

### 2. Secrets handling
- **No credentials in code, ever.** Not in defaults, not in tests, not in comments.
- Real values only in `.env` (gitignored).
- `.env.template` is checked in with empty values + comments describing each key.
- Never log a value from `.env` at INFO level. DEBUG only, and only the prefix (`APP_KEY[:8]…`).

### 3. Database schema management
- All schema changes go through Alembic migrations. No `CREATE TABLE` in application code.
- Migration files in `alembic/versions/`. Each migration is reversible (`upgrade()` + `downgrade()`).
- Never modify an already-applied migration — write a new one.

### 4. The FlashAlpha rule
- **GEX/DEX/VEX/CHEX are regime descriptors, not signals.** Per FlashAlpha backtest research, no single Greek exposure has predictive edge once ATM IV is controlled for.
- Do **not** generate alerts on raw GEX flip crossings, DEX migrations, or VEX changes alone.
- Alerts are emitted only by:
  - Strategy scanners that have been validated (jdintown, internals_composite, options_flow, fib)
  - The probability model (Phase 5+) that combines Greeks + VIX + ATM IV + credit spreads
- This rule is enforced architecturally: only `strategies/` modules write to `signals` table.

### 5. Idempotency for scheduled jobs
- Every scheduler job (`scheduler/jobs/*.py`) must be safely re-runnable.
- Use `INSERT ... ON CONFLICT DO NOTHING` for snapshot writes.
- Track job state in `scheduled_jobs_state` table, not in JSON files.

### 6. No commits with broken tests
- `pytest` must pass before any merge to `main`.
- GitHub Actions enforces this — but check locally first: `pytest -q`.

### 7. Cost-aware Claude usage
- Default model: `claude-sonnet-4-6`.
- Reserve `claude-opus-4-6` for weekly synthesis (`scheduler/jobs/weekly_themes.py`) and high-stakes ad-hoc analysis.
- Log token usage to `am_summaries.tokens_used` (and analog for other Claude calls).
- For chunk tagging during PDF ingestion: batch and use `sonnet`.

---

## Code style

- **Python 3.11+.** Use modern syntax (`match`, `|` union types, structural pattern matching where it improves clarity).
- **Type hints everywhere.** Public functions must have full annotations. Private helpers can elide return types if obvious.
- **Pydantic for DTOs**, dataclasses for internal value objects, SQLAlchemy ORM for DB models.
- **Black** for formatting (line length 100). **Ruff** for linting. Both wired into pre-commit.
- **Pytest** for tests. Fixtures in `tests/conftest.py`. Integration tests marked `@pytest.mark.integration` and skipped in CI unless `INTEGRATION=1`.

### File layout conventions
- Modules under 400 lines. If one grows past that, split it.
- One class per file unless the classes are tightly coupled (e.g., DTOs for one entity).
- Imports: stdlib → third-party → local. Each group alphabetized.
- No `from x import *`.

### Naming
- `snake_case` for functions, variables, modules.
- `PascalCase` for classes.
- `UPPER_SNAKE` for module-level constants.
- DB tables: plural snake_case (`greeks_snapshots`, not `greek_snapshot`).
- Test files mirror source: `tests/clients/test_convex.py` tests `trading_intel/clients/convex.py`.

---

## Architecture patterns

### Dependency injection
Every module that needs external services takes them in `__init__`. No module-level globals for clients.

```python
# Good
class GreeksJob:
    def __init__(self, source: OptionsDataSource, db: Session):
        self.source = source
        self.db = db

# Bad
from trading_intel.clients.convex import ConvexClient
client = ConvexClient()  # module-level — not allowed
```

### Wiring
The composition root is `trading_intel/scheduler/runner.py` (for the scheduler) and `trading_intel/dashboard/Home.py` (for the dashboard). All clients/sessions are instantiated there and passed into modules.

### Config
`trading_intel/config.py` exposes a single `Settings` pydantic-settings object. All env-derived values flow through it. No `os.getenv()` calls scattered through the codebase.

### Errors
- Domain errors: subclass `TradingIntelError` (in `trading_intel/errors.py`).
- External-service errors: catch at the `clients/` boundary, re-raise as `TradingIntelError` subclasses with original as `__cause__`.
- Never `except Exception: pass`. Either handle, log+re-raise, or let it propagate.

### Logging
- `structlog` with JSON output.
- Every job/request gets a `correlation_id` bound to context.
- Levels: DEBUG = dev only; INFO = state transitions; WARNING = recoverable; ERROR = needs attention; CRITICAL = page me.

---

## When making changes

### Adding a new data field from Convex
1. Add to `OptionsDataSource` Protocol in `clients/__init__.py`
2. Implement in `clients/convex.py`
3. Add Alembic migration if it persists
4. Update the relevant `greeks/`, `strategies/`, or `dashboard/` consumer
5. Test that mock and real implementations both return the new field

### Adding a new dashboard page
1. Create `trading_intel/dashboard/pages/N_Page_Name.py`
2. Import the data layer from `clients/` and analytics from `greeks/` or `strategies/`
3. Reuse components from `dashboard/components/`
4. Add an entry to the page table in `MEMORY.md`

### Adding a scheduled job
1. Create `scheduler/jobs/your_job.py` with a `run(session, source) -> None` function
2. Register in `scheduler/runner.py` with a cron expression
3. Make it idempotent
4. Add a corresponding test in `tests/strategies/` or `tests/scheduler/`

### Adding a new strategy
1. Create `trading_intel/strategies/your_strategy.py`
2. Implement the `SignalGenerator` Protocol (returns `list[Signal]`)
3. The strategy is allowed to write to the `signals` table
4. Document the logic in `docs/playbooks/your_strategy.md` (especially if it ports a PDF playbook)

### Touching an Alembic migration
- Never edit an already-applied migration.
- Write a new migration for any schema change.
- Test the `downgrade()` path: `alembic downgrade -1 && alembic upgrade head` should round-trip cleanly.

---

## Things to avoid

- **Pandas in production hot paths.** Use Polars or raw SQL for anything in the request/job critical path. Pandas is fine for dashboard rendering.
- **Synchronous HTTP in async contexts.** Use `httpx.AsyncClient`.
- **Logging values from `.env`.** See rule 2.
- **Storing PII or trade-secret content in commit messages.** PDFs in `data/pdfs/` may be proprietary — never copy excerpts into git history.
- **Adding npm/Node dependencies.** This is a pure Python project.
- **Adding more vendors.** The MASTER_PLAN.md fixes the vendor set. New vendors require an ADR in `docs/decisions/`.

---

## When asked to "make it work"

If a user (Mithil) asks you to "just make it work" or asks for shortcuts that conflict with these rules, push back gently and propose the rule-compliant version. The rules exist because this is a multi-week build and shortcuts compound into pain.

Example pushback:
> "I could hard-code the CONVEX_PASSWORD here to debug faster, but that violates rule 2 (secrets). The right path is to add it to your local `.env` and run from there. Takes 30 extra seconds, prevents a credential leak."

---

## Where to look first when debugging

| Symptom | Look at |
|---|---|
| Convex API errors | `trading_intel/clients/convex.py` + Convex rate-limit metrics in System Health page |
| Wrong Greek values | `trading_intel/greeks/exposures.py` + sample contract in `tests/greeks/test_exposures.py` |
| Scheduler not firing | `scheduler/runner.py` job registry + APScheduler logs |
| Dashboard slow | Check page's data loaders — should hit cached/normalized DB tables, not raw Convex calls |
| Migration drift | `alembic current` vs. `alembic heads` |
| Discord alerts not sending | `clients/discord.py` + webhook URL in `.env` |
| Wrong AM summary content | `synthesis/am_summary.py` + recent rows in `am_summaries` table |

---

## Quick links

- Master plan: `MASTER_PLAN.md`
- Working memory: `MEMORY.md`
- Deployment guide: `DEPLOYMENT.md`
- Decision log: `docs/decisions/`
- Learning notes for knowledge gaps: `docs/learning/`

---

*Last updated: May 19, 2026. Update this file whenever architectural rules change.*
