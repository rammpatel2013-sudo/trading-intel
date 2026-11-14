@echo off
REM ============================================================================
REM  run_iv_term.bat  --  per-name constant-maturity IV term structure
REM
REM  [1/2] Runs the collector unit tests.
REM  [2/2] Banks a constant-maturity IV term (ATM + 15/25 delta wings) at
REM        30/60/90 DTE per watchlist name, read from the STORED oi_chain_eod
REM        surface (no vendor call, no FMP). Writes into the shared
REM        iv_tenor_snapshots table, so get_iv_tenor surfaces it per name.
REM        Idempotent upsert on (symbol, ts, tenor_dte).
REM
REM  No migration needed (reuses the iv_tenor_snapshots table). Needs the .venv +
REM  DB reachable + a populated oi_chain_eod (run after the EOD chain snapshot).
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist alembic.ini (
    echo ERROR: expected repo root ^(alembic.ini not found next to this .bat^).
    exit /b 1
)
if not exist .venv\Scripts\python.exe (
    echo ERROR: .venv not found. Create it and `pip install -e .` first.
    exit /b 1
)

echo [1/2] Running collector unit tests...
.venv\Scripts\python -m pytest -q tests\scheduler\test_iv_term_snapshots.py
if errorlevel 1 (
    echo.
    echo Tests FAILED -- fix before banking data.
    exit /b 1
)

echo.
echo [2/2] Banking per-name constant-maturity IV term from oi_chain_eod...
.venv\Scripts\python -m trading_intel.scheduler.jobs.iv_term_snapshots
if errorlevel 1 (
    echo.
    echo IV-term snapshot FAILED -- is oi_chain_eod populated? Check DB reachability.
    exit /b 1
)

echo.
echo Done. Rows upserted into iv_tenor_snapshots (per-name, tenor 30/60/90).
echo The log line "iv_term_snapshots.done ... rows=N" shows how many were banked.
echo Read them back via the get_iv_tenor MCP tool, e.g. get_iv_tenor(symbols=["ORCL"]).
endlocal
