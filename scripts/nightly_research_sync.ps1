# Nightly research-folder -> dynamic-watchlist sync.
#
# Run on the LAPTOP (where Ollama lives). All config — including DATABASE_URL —
# is read from .env, so no secrets are hard-coded here (CLAUDE.md rule 2).
#
# Wired to Windows Task Scheduler to run nightly at 02:00 (see the
# Register-ScheduledTask command in the deploy notes). Safe to run manually too:
#     powershell -ExecutionPolicy Bypass -File scripts\nightly_research_sync.ps1
#
# Prerequisites:
#   * Ollama running locally (it normally runs in the background after install)
#   * .env DATABASE_URL pointed at the NAS Postgres
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
& ".\.venv\Scripts\python.exe" "scripts\sync_research_watchlist.py" @args
