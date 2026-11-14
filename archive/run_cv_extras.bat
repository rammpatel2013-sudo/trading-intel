@echo off
REM ============================================================================
REM  run_cv_extras.bat  --  ConvexValue "extras" digest
REM
REM  Pulls the ConvexValue endpoints beyond convexlib's core -- earnings +
REM  economic calendars, native vflowratio flow scan, per-name IV term structure,
REM  and index net-flow -- via the same pro login, into
REM  reports\cv_extras_<date>.html, then opens it. Descriptive only (rule 4).
REM
REM      run_cv_extras.bat
REM
REM  Needs CONVEX_EMAIL + CONVEX_PASSWORD in .env + the .venv.
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

echo Pulling ConvexValue extras...
.venv\Scripts\python scripts\cv_extras.py
if errorlevel 1 (
    echo.
    echo cv_extras FAILED -- check CONVEX_EMAIL/CONVEX_PASSWORD in .env and your connection.
    exit /b 1
)

REM open the newest digest in the default browser
for /f "delims=" %%F in ('dir /b /o-d reports\cv_extras_*.html 2^>nul') do (
    start "" "reports\%%F"
    goto :done
)
:done
endlocal
