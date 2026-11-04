@echo off
REM restore_20261104_pre_phase3.bat - created 2026-11-04. RESTORE POINT for Phase 3.
REM Puts the repo back to checkpoint-20261104-pre-phase3 (Phase 2 + Side toggle, before Phase 3) as a NEW commit.
REM No DB change in Phase 3, so no alembic step. Run ONLY if you want Phase 3 gone. Delete once Phase 3 is confirmed good.
cd /d C:\Users\drmit\PycharmProjects\trading-intel || exit /b 1
git fetch origin --tags || exit /b 1
git checkout checkpoint-20261104-pre-phase3 -- scripts/cvxmap/cvx_cockpit.html scripts/cvxmap/cvx_live.py scripts/cvxmap/p1score.py scripts/cvx_replay.py tests/cvxmap/test_p1score.py || exit /b 1
git commit -m "restore: back to checkpoint-20261104-pre-phase3 (Phase 3 removed)" || exit /b 1
git push origin main || exit /b 1
echo RESTORED. NAS: tarball pull, then restart the cockpit (cvxmap stopall / start) or wait for 09:25.
