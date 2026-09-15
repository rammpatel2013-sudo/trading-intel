@echo off
REM ============================================================================
REM  trading-intel — ONE-COMMAND end-to-end ship (laptop -> GitHub -> DB -> NAS)
REM
REM  Usage:
REM    scripts\ship.bat "commit message" "nas jobs" path\file1 [path\file2 ...]
REM    scripts\ship.bat "commit message" "nas jobs" --staged      <- no path list
REM
REM  POWERSHELL TRAPS (both silent, both bite):
REM    1. line continuation is a BACKTICK, not ^ . A ^ makes PowerShell run this
REM       with NO file args (usage error) and then execute each following line as
REM       its own command.
REM    2. PowerShell DROPS a literal "" argument, shifting every path left by one
REM       -- your first file becomes the <nas jobs> value. Pass none, never "".
REM    Safest from PowerShell: put paths in an array and splat, or use --staged.
REM       $f = @('"'"'scripts\nas\run_job.sh'"'"','"'"'trading_intel\config.py'"'"')
REM       scripts\ship.bat "msg" none $f
REM
REM  Example:
REM    scripts\ship.bat "feat: breadth + report fix" "breadth cockpit_report sector_report" ^
REM      scripts\cockpit_report.py trading_intel\scheduler\jobs\breadth.py alembic\versions\0043_breadth_snapshots.py
REM
REM  Does, in order:
REM    1. scripts\deploy.bat  -> py_compile + git add EXACT paths + commit + push
REM    2. alembic upgrade head -> apply any new migration (shared DB = live on NAS;
REM                               idempotent, a no-op when already at head)
REM    3. ssh -t NAS -> deploy.sh --run "<jobs>"  (rebuild image + pull scripts +
REM                               fire the jobs; posts reports to Telegram)
REM  You will be prompted once for the NAS login and once for sudo — that is the
REM  interactive password the fully-unattended path can't avoid. Pass an empty
REM  "" for <nas jobs> to rebuild without firing any job.
REM  NOTE: requires deploy.sh on the NAS to be the greedy --run build (2026-08-11+).
REM        For the FIRST ship that introduces it, run the NAS step by hand once
REM        (see pending-deploys / deploy-automation), then ship.bat works forever.
REM ============================================================================
setlocal enabledelayedexpansion
REM capture the script dir NOW — the arg-collect `shift` loop below also shifts %0,
REM so %~dp0 must not be read after it (it would drift to a file arg's folder).
set "HERE=%~dp0"
cd /d "%HERE%.."

if "%~1"=="" goto usage
set "MSG=%~1"
set "JOBS=%~2"
REM no-jobs sentinel = "none" / "-" / empty. IMPORTANT: PowerShell DROPS a literal
REM "" argument (shifting everything left), so from PowerShell pass "none", not "".
if /i "!JOBS!"=="none" set "JOBS="
if "!JOBS!"=="-" set "JOBS="
shift
shift

set "FILES="
:collect
if "%~1"=="" goto done_collect
set FILES=!FILES! "%~1"
shift
goto collect
:done_collect
if "!FILES!"=="" goto usage

echo ==== [1/3] laptop: compile + commit + push ====
call "%HERE%deploy.bat" "!MSG!" !FILES!
if !errorlevel! neq 0 ( echo [FAIL] laptop deploy — aborting. & exit /b 1 )

echo.
echo ==== [2/3] migrate: alembic upgrade head ====
alembic upgrade head
if !errorlevel! neq 0 ( echo [FAIL] alembic upgrade — aborting before NAS. & exit /b 1 )

echo.
echo ==== [3/3] NAS: rebuild + run jobs (enter NAS + sudo password when prompted) ====
set "RUNARG="
if defined JOBS set "RUNARG=--run !JOBS!"
ssh -t drmithil@192.168.1.211 "sudo sh /var/services/homes/drmithil/trading-intel/scripts/nas/deploy.sh !RUNARG!"
if !errorlevel! neq 0 ( echo [WARN] NAS step returned nonzero — check output above. & exit /b 1 )

echo.
echo ============================================================
echo  SHIP COMPLETE: pushed, migrated, NAS rebuilt + jobs fired.
echo  Restart Claude Desktop to pick up any new MCP tools.
echo ============================================================
goto :eof

:usage
echo Usage: scripts\ship.bat "commit message" "nas jobs" path\file1 [path\file2 ...]
echo   "nas jobs" = space-separated scheduler jobs to fire after build (or "" for none).
exit /b 1
