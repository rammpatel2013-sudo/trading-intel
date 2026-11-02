@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM  Ship 1 — Data Integrity Deploy
REM  Run this from your trading-intel checkout on the LAPTOP.
REM  It pulls the latest code and rebuilds the NAS Docker image.
REM ═══════════════════════════════════════════════════════════════════════

echo.
echo ╔═══════════════════════════════════════════════════════════════╗
echo ║           Ship 1 — Data Integrity Deploy                     ║
echo ╚═══════════════════════════════════════════════════════════════╝
echo.

REM ─── Step 1: Pull latest code ─────────────────────────────────────────
echo [1/4] Pulling latest from GitHub...
cd /d C:\Users\drmit\PycharmProjects\trading-intel
git pull origin main
if errorlevel 1 (
    echo ERROR: git pull failed. Check your network or resolve merge conflicts.
    pause
    exit /b 1
)
echo      OK — code is up to date.
echo.

REM ─── Step 2: Verify key files exist ───────────────────────────────────
echo [2/4] Verifying Ship 1 files...
set MISSING=0
if not exist "trading_intel\scheduler\jobs\norseman_regime.py" set MISSING=1
if not exist "trading_intel\scheduler\jobs\oi_chain_eod.py" set MISSING=1
if not exist "trading_intel\clients\prices.py" set MISSING=1
if not exist "trading_intel\synthesis\newsletter_extract.py" set MISSING=1
if not exist "scripts\nas\run_job.sh" set MISSING=1
if %MISSING% == 1 (
    echo ERROR: Key files missing. Did the pull fail?
    pause
    exit /b 1
)
echo      OK — all Ship 1 files present.
echo.

REM ─── Step 3: Run local tests ──────────────────────────────────────────
echo [3/4] Running local tests (strategies + clients + greeks)...
python -m pytest tests/strategies/ tests/clients/ tests/greeks/ tests/market/ -q --tb=short
if errorlevel 1 (
    echo WARNING: Some tests failed. Review output above.
    echo Press any key to continue anyway, or Ctrl+C to abort.
    pause
)
echo      Tests done.
echo.

REM ─── Step 4: Manual steps reminder ────────────────────────────────────
echo [4/4] MANUAL STEPS (do these yourself):
echo.
echo   A) NAS .env — add these two lines to /volume1/docker/trading-intel/.env
echo      if they aren't there already:
echo.
echo        NAS_DATABASE_URL=postgresql+psycopg://intel:YOUR_PASSWORD@trading-intel-pg:5432/trading_intel
echo        NAS_OLLAMA_URL=http://192.168.1.175:11434
echo.
echo   B) NAS Docker rebuild — SSH into the NAS and run:
echo.
echo        cd /volume1/docker/trading-intel
echo        # Download latest tarball:
echo        curl -L https://github.com/rammpatel2013-sudo/trading-intel/archive/refs/heads/main.tar.gz -o main.tar.gz
echo        tar xzf main.tar.gz --strip-components=1
echo        docker build --no-cache -t trading-intel .
echo.
echo   C) DSM Task Scheduler — re-enable the preflight task (#9)
echo      if you disabled it during debugging.
echo.
echo   D) Verify after next trading session:
echo      - Check greeks_snapshot rows have source='convex' only
echo      - Check oi_chain_eod queries filter source LIKE 'convex%%'
echo      - Check vol_regime signal in signals table
echo      - Check norseman_regime doesn't fire on weekends
echo      - Check AM summary newsletter levels are in-range
echo.

echo ═══════════════════════════════════════════════════════════════
echo  Ship 1 deploy complete. Review manual steps above.
echo ═══════════════════════════════════════════════════════════════
pause
