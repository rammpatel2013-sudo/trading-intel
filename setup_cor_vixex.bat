@echo off
REM ============================================================================
REM  setup_cor_vixex.bat  --  apply + seed the COR1m/COR3m + VIX-expiration work
REM  Run from the repo root with the venv ACTIVE:
REM      .\setup_cor_vixex.bat
REM  Stops on the first failing step. Re-runnable (all steps are idempotent).
REM ============================================================================
setlocal

echo.
echo === [0/6] sanity: python + repo root ===
python -c "import trading_intel, sys; print('python', sys.version.split()[0])" || goto :error
if not exist alembic.ini (
    echo ERROR: run this from the repo root ^(alembic.ini not found^).
    goto :error
)

echo.
echo === [1/6] alembic: current revision ===
alembic current || goto :error

echo.
echo === [2/6] alembic: upgrade to head ^(0025 cor cols + 0026 vix_expirations^) ===
alembic upgrade head || goto :error

echo.
echo === [3/6] unit tests for the new pure logic ===
pytest tests\vol\test_vix_calendar.py tests\scheduler\test_vix_expirations.py -q || goto :error

echo.
echo === [4/6] seed the VIX expiration calendar ^(deterministic, no vendor call^) ===
python -m trading_intel.scheduler.jobs.vix_expirations || goto :error

echo.
echo === [5/6] backfill recoverable history: quotes + COR/Nations indices ===
python scripts\backfill_quotes.py || goto :error
python scripts\backfill_index_skew.py --period 5y || goto :error

echo.
echo === [6/6] run today's index_skew so COR1M/COR3M populate now ===
python -m trading_intel.scheduler.jobs.index_skew || goto :error

echo.
echo === DONE. Open the VIX dashboard page to see the new panels. ===
echo     Full suite ^(optional^):  pytest -q
goto :eof

:error
echo.
echo *** FAILED at the step above. Fix it, then re-run -- the script is idempotent. ***
exit /b 1
