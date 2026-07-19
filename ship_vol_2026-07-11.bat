@echo off
REM ============================================================================
REM  ship_vol_2026-07-11.bat  --  one-click ship of the 2026-07-11 vol work:
REM    * dispersion fields surfaced in get_index_skew (MCP)
REM    * rv_rolloff_projection + get_rv_rolloff MCP tool + page 19_RV_Rolloff
REM    * longitudinal EOD flow report core + get_flow_report MCP tool
REM
REM  Runs the targeted tests, formats/lints ONLY the changed files, then makes
REM  scoped commits on a FEATURE BRANCH (never main) and pushes it for a PR.
REM  Commits ONLY the files named below, so your other in-flight work is left
REM  untouched. Stops on the first failure. Re-runnable.
REM
REM  Run from the repo root with the venv present:
REM      .\ship_vol_2026-07-11.bat
REM ============================================================================
setlocal
set BRANCH=feature/vol-rolloff-flow-report

echo.
echo === [0/5] sanity: repo root + venv ===
if not exist alembic.ini (
    echo ERROR: run this from the repo root ^(alembic.ini not found^).
    goto :error
)
call .venv\Scripts\activate.bat || goto :error

echo.
echo === [1/5] targeted tests (the changed surfaces) ===
python -m pytest tests\prices\test_realized_vol.py tests\mcp\test_extra_tools.py tests\flow\test_report.py -q || goto :error

echo.
echo === [2/5] format + lint ONLY the changed files ===
set FILES=trading_intel\prices\realized_vol.py trading_intel\mcp\extra_tools.py trading_intel\mcp\server.py trading_intel\flow\report.py trading_intel\dashboard\pages\19_RV_Rolloff.py tests\prices\test_realized_vol.py tests\mcp\test_extra_tools.py tests\flow\test_report.py
black %FILES% || goto :error
ruff check %FILES% || goto :error

echo.
echo === [3/5] feature branch ===
git rev-parse --is-inside-work-tree >nul 2>&1 || ( echo ERROR: not a git repo. & goto :error )
git checkout -b %BRANCH% 2>nul || git checkout %BRANCH% || goto :error

echo.
echo === [4/5] scoped commits (only these files) ===
git add trading_intel\prices\realized_vol.py tests\prices\test_realized_vol.py
git diff --cached --quiet || git commit -m "prices: add rv_rolloff_projection (trailing-window RV roll-off)" || goto :error
git add trading_intel\mcp\extra_tools.py trading_intel\mcp\server.py tests\mcp\test_extra_tools.py
git diff --cached --quiet || git commit -m "mcp: surface dispersion in get_index_skew; add get_rv_rolloff + get_flow_report" || goto :error
git add trading_intel\flow\report.py tests\flow\test_report.py
git diff --cached --quiet || git commit -m "flow: add longitudinal EOD flow report (trend/lifecycle/churn)" || goto :error
git add trading_intel\dashboard\pages\19_RV_Rolloff.py
git diff --cached --quiet || git commit -m "dashboard: add RV Roll-off projection page" || goto :error
git add docs\deploy_2026-07-11_vol_rolloff.md docs\learning\vol-newsletter-digest-2026-07-11.md
git diff --cached --quiet || git commit -m "docs: vol newsletter digest + rv-rolloff/flow deploy runbook" || goto :error

echo.
echo === [5/5] push the branch (creates a PR link) ===
git push -u origin %BRANCH% || goto :error

echo.
echo === DONE. Branch %BRANCH% pushed. Open the PR link GitHub printed above. ===
echo   * CI runs the full pytest/ruff/black suite on the PR.
echo   * After merge: restart Claude Desktop (registers get_rv_rolloff + get_flow_report),
echo     restart Streamlit (loads page 19). No migration / no NAS rebuild needed.
goto :eof

:error
echo.
echo *** FAILED at the step above. Nothing was pushed. Fix it, then re-run. ***
exit /b 1
