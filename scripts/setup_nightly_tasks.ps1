<#
.SYNOPSIS
  Register the three nightly trading-intel jobs in Windows Task Scheduler.

.DESCRIPTION
  These jobs run on THIS laptop (not the NAS) because they need local Ollama:

    02:00  watchlist_ingest research\company   - ingest any NEW research PDFs -> dynamic watchlist
    02:15  research_notes                       - per-ticker narrative research note (PDF + 10-K + regime)
    02:30  surface_reports                      - per-ticker interpretive surface + flow report

  Each task runs the project's venv Python with the repo as the working dir.
  StartWhenAvailable = if the laptop was asleep/off at the trigger time, the task
  runs as soon as it next wakes. Generation is slow CPU Ollama, so overnight is fine.

  NOTE: nightly ingest is intentionally NOT --force (it only picks up new files).
  To re-process an already-ingested PDF through improved extraction, run manually:
      python -m trading_intel.memory.watchlist_ingest research\company --force

.NOTES
  Run once, from an ADMIN PowerShell, from the repo root:
      powershell -ExecutionPolicy Bypass -File scripts\setup_nightly_tasks.ps1
  Re-running is safe: it unregisters and recreates the tasks.
#>

# --- Resolve paths (script lives in <repo>\scripts) -------------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Error "venv Python not found at $PythonExe. Activate/create the project .venv first."
    exit 1
}

# Run as the current interactive user (Ollama + .env live in this profile).
$User = "$env:USERDOMAIN\$env:USERNAME"
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

# Shared settings: if the machine was off at trigger time, run on next wake.
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

# Job table: name, time, module, args
$Jobs = @(
    @{ Name = "TradingIntel-WatchlistIngest"; Time = "02:00"; Module = "trading_intel.memory.watchlist_ingest"; Args = "research\company" },
    @{ Name = "TradingIntel-ResearchNotes";   Time = "02:15"; Module = "trading_intel.scheduler.jobs.research_notes"; Args = "" },
    @{ Name = "TradingIntel-SurfaceReports";  Time = "02:30"; Module = "trading_intel.scheduler.jobs.surface_reports"; Args = "" }
)

foreach ($job in $Jobs) {
    $argline = "-m $($job.Module)"
    if ($job.Args) { $argline += " $($job.Args)" }

    $Action = New-ScheduledTaskAction `
        -Execute $PythonExe `
        -Argument $argline `
        -WorkingDirectory $RepoRoot

    $Trigger = New-ScheduledTaskTrigger -Daily -At $job.Time

    # Remove an existing task of the same name so re-running is idempotent.
    Unregister-ScheduledTask -TaskName $job.Name -Confirm:$false -ErrorAction SilentlyContinue

    Register-ScheduledTask `
        -TaskName $job.Name `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description "trading-intel nightly: $($job.Module)" | Out-Null

    Write-Host "Registered $($job.Name) -> daily $($job.Time)  ($argline)"
}

Write-Host ""
Write-Host "Done. Inspect with:  Get-ScheduledTask -TaskName 'TradingIntel-*'"
Write-Host "Test one now with:    Start-ScheduledTask -TaskName 'TradingIntel-ResearchNotes'"
