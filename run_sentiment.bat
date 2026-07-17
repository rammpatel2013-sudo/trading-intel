@echo off
REM ============================================================================
REM  run_sentiment.bat  --  institutional 13F + analyst sentiment snapshot
REM
REM  [1/3] Applies migrations (creates sentiment_snapshots via 0034 if needed).
REM  [2/3] Runs the unit tests for the collector.
REM  [3/3] Banks one row per (symbol, ts) for the sentiment universe
REM        (SENTIMENT_UNIVERSE in .env, else the WATCHLIST): institutional
REM        ownership + analyst price-target/rating consensus, pulled from CVForge
REM        FMP. Idempotent weekly upsert. Descriptor only (FlashAlpha rule 4).
REM
REM        run_sentiment.bat
REM
REM  Writes to the NAS Postgres named in .env DATABASE_URL (the single DB), so
REM  running this from the laptop is enough to create the table + first rows.
REM  Needs CVFORGE_API_KEY in .env (Research tier) + the .venv + DB reachable.
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

echo [1/3] Applying migrations ^(alembic upgrade head^)...
.venv\Scripts\python -m alembic upgrade head
if errorlevel 1 (
    echo.
    echo Alembic upgrade FAILED -- check DATABASE_URL in .env and that the NAS Postgres is reachable.
    exit /b 1
)

echo.
echo [2/3] Running collector unit tests...
.venv\Scripts\python -m pytest -q tests\sentiment tests\scheduler\test_sentiment.py
if errorlevel 1 (
    echo.
    echo Tests FAILED -- fix before banking data.
    exit /b 1
)

echo.
echo [3/3] Banking institutional + analyst sentiment from CVForge...
.venv\Scripts\python -m trading_intel.scheduler.jobs.sentiment
if errorlevel 1 (
    echo.
    echo Sentiment snapshot FAILED -- check CVFORGE_API_KEY in .env and your connection.
    exit /b 1
)

echo.
echo Done. Rows upserted into sentiment_snapshots ^(idempotent on symbol, ts^).
echo The job log line "sentiment.done ... rows=N" shows how many names were banked.
echo If rows^>0 but every field is null, the FMP /stable endpoint spellings need
echo confirming -- see the NOTE in trading_intel\sentiment\fmp_map.py.
endlocal
