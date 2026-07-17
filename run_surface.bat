@echo off
REM ============================================================================
REM  run_surface.bat  --  full vol surface snapshot (SPX / QQQ / SPY)
REM
REM  [1/3] alembic upgrade head  (creates surface_snapshots via migration 0035)
REM  [2/3] collector unit tests
REM  [3/3] bank the whole delta x expiry surface for the index ETFs from a LIVE
REM        chain -> so RUN AFTER THE CLOSE. Idempotent upsert on
REM        (symbol, ts, expiry_date, moneyness). Feeds the Vol Surface page (20).
REM
REM  Needs the .venv + DB reachable + CVForge/Convex creds in .env. The changes
REM  board needs TWO days banked, so run today and again tomorrow.
REM ============================================================================
setlocal
cd /d "%~dp0"

if not exist alembic.ini ( echo ERROR: repo root not found. & exit /b 1 )
if not exist .venv\Scripts\python.exe ( echo ERROR: .venv not found. & exit /b 1 )

echo [1/3] Applying migrations ^(alembic upgrade head^)...
.venv\Scripts\python -m alembic upgrade head
if errorlevel 1 ( echo. & echo Alembic upgrade FAILED -- check DATABASE_URL / NAS Postgres. & exit /b 1 )

echo.
echo [2/3] Running collector unit tests...
.venv\Scripts\python -m pytest -q tests\scheduler\test_surface_snapshots.py
if errorlevel 1 ( echo. & echo Tests FAILED. & exit /b 1 )

echo.
echo [3/3] Banking the full vol surface (live chain -- after the close)...
.venv\Scripts\python -m trading_intel.scheduler.jobs.surface_snapshots
if errorlevel 1 ( echo. & echo Surface snapshot FAILED -- check the chain pull / connection. & exit /b 1 )

echo.
echo Done. Rows upserted into surface_snapshots. Log "surface_snapshots.done ... rows=N".
echo View it: run the dashboard and open page 20 (Vol Surface). Changes appear once a
echo second day is banked.  Dashboard:  .venv\Scripts\streamlit run trading_intel\dashboard\Home.py
endlocal
