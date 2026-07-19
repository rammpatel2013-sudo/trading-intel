@echo off
REM ============================================================================
REM  run_swing_report.bat  --  on-demand swing-setup scanner (Stage-1, live CVForge)
REM
REM  Pulls a LIVE chain (greeks + IV + OI), realized-vol history, and technicals
REM  from CVForge (ADR-004) for the watchlist (or the tickers you pass), scores
REM  each setup, picks a defined-risk option structure, and writes
REM  reports\swing_<date>.html, then opens it. convexlib is NOT touched.
REM
REM  Descriptive candidates only -- not signals, not advice (FlashAlpha rule 4).
REM
REM  Run from anywhere (it cd's to its own folder = repo root):
REM      run_swing_report.bat                 (whole WATCHLIST)
REM      run_swing_report.bat AAPL NVDA TSLA  (specific names)
REM
REM  Needs CVFORGE_API_KEY in .env (Go/Research tier) + the .venv.
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

echo Pulling live swing setups from CVForge...
.venv\Scripts\python scripts\swing_report.py %*
if errorlevel 1 (
    echo.
    echo Swing report FAILED -- check CVFORGE_API_KEY in .env and your connection.
    exit /b 1
)

REM open the newest report in the default browser
for /f "delims=" %%F in ('dir /b /o-d reports\swing_*.html 2^>nul') do (
    start "" "reports\%%F"
    goto :done
)
:done
endlocal
