@echo off
REM ============================================================================
REM  run_swing_features.bat  --  daily swing feature-snapshot collector
REM
REM  Banks the per-name Stage-1 feature vector (IV/RV, RSI, 25d skew, net GEX/DEX
REM  + their trailing-252d percentiles) into the swing_features table so the
REM  percentile features mature into a real distribution for the Stage-2 model.
REM  Fed by CVForge (ADR-004); convexlib is NOT touched. Descriptive only
REM  (FlashAlpha rule 4). Idempotent -- safe to re-run; run once per trading day.
REM
REM  Schedule it (Windows Task Scheduler or a NAS DSM task) for reliable daily
REM  banking -- the percentiles only mature if it runs every trading day.
REM
REM      run_swing_features.bat                 (whole WATCHLIST)
REM      run_swing_features.bat AAPL NVDA       (specific names)
REM
REM  Needs CVFORGE_API_KEY in .env + the .venv + `alembic upgrade head` (0031).
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

echo Banking swing features from CVForge...
.venv\Scripts\python scripts\swing_features.py %*
if errorlevel 1 (
    echo.
    echo Swing feature collection FAILED -- check CVFORGE_API_KEY/.env, the DB,
    echo and that `alembic upgrade head` has applied migration 0031.
    exit /b 1
)
endlocal
