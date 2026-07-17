@echo off
REM ============================================================================
REM  ship_flow_report_2026-07-12.bat  --  one-click ship of the EOD Flow Report:
REM    * scripts/flow_report.py  (HTML report + Key Findings)
REM    * reports.build_flow shim + generate_flow_report MCP tool
REM    * get_rv_rolloff + page 19 default -> SPY (SPX quotes go stale)
REM
REM  Runs the report tests, formats/lints ONLY the changed files, then makes
REM  scoped commits on a FEATURE BRANCH (never main) and pushes it for a PR.
REM  Stops on the first failure. Re-runnable. Run from the repo root:
REM      .\ship_flow_report_2026-07-12.bat
REM ============================================================================
setlocal
set BRANCH=feature/eod-flow-report

echo.
echo === [0/5] sanity: repo root + venv ===
if not exist alembic.ini ( echo ERROR: run from repo root ^(alembic.ini not found^). & goto :error )
call .venv\Scripts\activate.bat || goto :error

echo.
echo === [1/5] tests ===
python -m pytest tests\flow\test_report.py tests\flow\test_flow_report_html.py tests\mcp\test_extra_tools.py -q || goto :error

echo.
echo === [2/5] format + lint ONLY the changed files ===
set FILES=scripts\flow_report.py trading_intel\reports.py trading_intel\mcp\server.py trading_intel\mcp\extra_tools.py trading_intel\dashboard\pages\19_RV_Rolloff.py tests\flow\test_flow_report_html.py
black %FILES% || goto :error
ruff check %FILES% || goto :error

echo.
echo === [3/5] feature branch ===
git rev-parse --is-inside-work-tree >nul 2>&1 || ( echo ERROR: not a git repo. & goto :error )
git checkout -b %BRANCH% 2>nul || git checkout %BRANCH% || goto :error

echo.
echo === [4/5] scoped commits ===
git add scripts\flow_report.py tests\flow\test_flow_report_html.py trading_intel\reports.py
git diff --cached --quiet || git commit -m "flow: add EOD HTML flow report (key findings + generate_flow_report)" || goto :error
git add trading_intel\mcp\server.py trading_intel\mcp\extra_tools.py
git diff --cached --quiet || git commit -m "mcp: add generate_flow_report; default get_rv_rolloff to SPY" || goto :error
git add trading_intel\dashboard\pages\19_RV_Rolloff.py
git diff --cached --quiet || git commit -m "dashboard: default RV roll-off page to SPY" || goto :error

echo.
echo === [5/5] push the branch (creates a PR link) ===
git push -u origin %BRANCH% || goto :error

echo.
echo === DONE. Branch %BRANCH% pushed. Open the PR link GitHub printed above. ===
echo   After merge: restart Claude Desktop (registers generate_flow_report,
echo   get_flow_report, get_rv_rolloff). Generate a report from Claude Desktop with
echo   "generate_flow_report" or run:  python scripts\flow_report.py
goto :eof

:error
echo.
echo *** FAILED at the step above. Nothing was pushed. Fix it, then re-run. ***
exit /b 1
