@echo off
REM ============================================================================
REM  trading-intel — one-go LAPTOP deploy
REM  Usage:  scripts\deploy.bat "commit message" path\to\file1 [path\to\file2 ...]
REM     or:  scripts\deploy.bat "commit message" --staged
REM
REM  --staged commits WHAT YOU ALREADY STAGED with git, instead of taking paths.
REM  Stage however you like (git add <paths>, git add -p, tab completion, an IDE)
REM  then ship. Still never -A: you choose what goes in, git just remembers it,
REM  so a 150-file CRLF-churn repo stays safe without retyping a path list.
REM
REM  - Compiles any .py you pass (fails fast on a syntax error).
REM  - git-adds EXACTLY those paths (NEVER -A: the repo has heavy CRLF churn).
REM  - commits + pushes origin/main.
REM  - Prints the single NAS command to finish the deploy.
REM  Migrations are separate & rare: run `alembic upgrade head` yourself when a
REM  new alembic/versions/*.py is part of the change (shared DB -> live on NAS).
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0.."

if "%~1"=="" goto usage
set "MSG=%~1"
shift

REM --- staged mode: commit the existing index, don't build a path list --------
if /i "%~1"=="--staged" (
  set "ANY="
  for /f "delims=" %%F in ('git diff --cached --name-only --diff-filter=d') do (
    set "ANY=1"
    echo %%F | findstr /i /r "\.py$" >nul
    if !errorlevel! equ 0 (
      python -m py_compile "%%F"
      if !errorlevel! neq 0 ( echo [FAIL] compile: %%F & exit /b 1 )
    )
  )
  if not defined ANY (
    echo [FAIL] --staged but nothing is staged. Stage first, e.g.:
    echo        git add scripts\nas\run_job.sh trading_intel\config.py
    exit /b 1
  )
  echo [1/3] using the staged set ^(git add skipped^):
  git diff --cached --name-only
  goto do_commit
)

set "FILES="
:collect
if "%~1"=="" goto done_collect
set "F=%~1"
echo !F! | findstr /i /r "\.py$" >nul
if !errorlevel! equ 0 (
  python -m py_compile "!F!"
  if !errorlevel! neq 0 ( echo [FAIL] compile: !F! & exit /b 1 )
)
set FILES=!FILES! "!F!"
shift
goto collect
:done_collect

if "!FILES!"=="" goto usage

echo [1/3] git add (explicit paths only) ...!FILES!
git add !FILES!
if !errorlevel! neq 0 exit /b 1

:do_commit
echo [2/3] git commit
git commit -m "!MSG!"
if !errorlevel! neq 0 exit /b 1
echo [3/3] git push origin main
git push origin main
if !errorlevel! neq 0 exit /b 1

echo.
echo ============================================================
echo  Pushed to origin/main. Finish on the NAS with ONE command:
echo    ssh drmithil@192.168.1.211
echo    sudo sh /var/services/homes/drmithil/trading-intel/scripts/nas/deploy.sh
echo.
echo  Append flags as needed:
echo    --run weekly_swing_dossiers   build, then fire the job (posts to Telegram)
echo    --no-build                    scripts/-only layout change (skip image build)
echo ============================================================
goto :eof

:usage
echo Usage: scripts\deploy.bat "commit message" path\to\file1 [path\to\file2 ...]
echo    or: scripts\deploy.bat "commit message" --staged
echo   Compiles .py args, git-adds EXACTLY those paths (never -A), commits, pushes.
echo   --staged commits whatever you already staged with git instead.
exit /b 1
