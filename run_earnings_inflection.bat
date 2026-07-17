@echo off
REM ============================================================================
REM  run_earnings_inflection.bat  --  earnings-call inflection scan (Slice 1)
REM
REM  Pulls the two most recent earnings-call transcripts per name (free via the
REM  CVForge FMP passthrough), measures the quarter-over-quarter tone change +
REM  guidance raise/cut cues, and writes reports\earnings_inflection_<date>.html
REM  ranked by a positive/negative inflection read, then opens it.
REM
REM  Descriptive candidates only -- not signals, not advice (FlashAlpha rule 4).
REM
REM      run_earnings_inflection.bat                 (whole WATCHLIST)
REM      run_earnings_inflection.bat AAPL NVDA TSLA  (specific names)
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

echo Scanning earnings-call inflection from CVForge transcripts...
.venv\Scripts\python scripts\earnings_inflection.py %*
if errorlevel 1 (
    echo.
    echo Earnings inflection scan FAILED -- check CVFORGE_API_KEY in .env and your connection.
    exit /b 1
)

for /f "delims=" %%F in ('dir /b /o-d reports\earnings_inflection_*.html 2^>nul') do (
    start "" "reports\%%F"
    goto :done
)
:done
endlocal
