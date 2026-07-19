@echo off
REM ============================================================================
REM  run_credit_income_scan.bat  --  Track B market-wide credit-income scanner
REM
REM  Ranks a BROAD universe (watchlist + a liquid set; widen with --screen) for
REM  defined-risk CREDIT structures where implied vol is rich vs realized, using
REM  a live CVForge pull (ADR-004). Writes reports\credit_income_<date>.html and
REM  opens it. convexlib is NOT touched. Sibling to run_swing_report.bat
REM  (Track A = cheap-vol debit setups).
REM
REM  Descriptive candidates only -- not signals, not advice (FlashAlpha rule 4).
REM
REM  Run from anywhere (it cd's to its own folder = repo root):
REM      run_credit_income_scan.bat                 (watchlist + broad set)
REM      run_credit_income_scan.bat SPY QQQ AMD      (specific names)
REM      run_credit_income_scan.bat --screen         (widen via CVForge /screen)
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

echo Scanning market-wide credit-income candidates from CVForge...
.venv\Scripts\python scripts\credit_income_scan.py %*
if errorlevel 1 (
    echo.
    echo Credit-income scan FAILED -- check CVFORGE_API_KEY in .env and your connection.
    exit /b 1
)

REM open the newest report in the default browser
for /f "delims=" %%F in ('dir /b /o-d reports\credit_income_*.html 2^>nul') do (
    start "" "reports\%%F"
    goto :done
)
:done
endlocal
