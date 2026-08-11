@echo off
REM ============================================================================
REM  trading-intel — ONE-COMMAND end-to-end ship (laptop -> GitHub -> DB -> NAS)
REM
REM  Usage:
REM    scripts\ship.bat "commit message" "nas jobs" path\file1 [path\file2 ...]
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
cd /d "%~dp0.."

if "%~1"=="" goto usage
set "MSG=%~1"
set "JOBS=%~2"
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
call "%~dp0deploy.bat" "!MSG!" !FILES!
if !errorlevel! neq 0 ( echo [FAIL] laptop deploy — aborting. & exit /b 1 )

echo.
echo ==== [2/3] migrate: alembic upgrade head ====
alembic upgrade head
if !errorlevel! neq 0 ( echo [FAIL] alembic upgrade — aborting before NAS. & exit /b 1 )

echo.
echo ==== [3/3] NAS: rebuild + run jobs (enter NAS + sudo password when prompted) ====
ssh -t drmithil@192.168.1.211 "sudo sh /var/services/homes/drmithil/trading-intel/scripts/nas/deploy.sh --run !JOBS!"
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
