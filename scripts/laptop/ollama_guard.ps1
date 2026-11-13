<#
  ollama_guard.ps1  -  made 2026-11-12 (trading-intel, LAPTOP only)

  Keeps Ollama CLOSED on weekdays 09:30-16:15 (trading hours) and OPEN the rest
  of the time. 2026-11-13: the quiet window starts at 09:30 (was 08:00) so the
  08:00-09:30 morning Doc read can use the AI; in that window the NAS side lets
  ONLY the Doc read call Ollama (trading_intel/synthesis/ollama_lock.py). Ollama is the local AI that the NAS report jobs call (newsletter
  levels, Jaguar summaries, evening report comments). While it is closed those
  jobs skip their AI part, so no AI work lands on this laptop while you trade.

  Usage (Windows PowerShell 5.1):
    ollama_guard.ps1              apply the rule now (the task and log-in run this)
    ollama_guard.ps1 -Install     one-time set-up (setup_20261112_ollama_guard.bat)
    ollama_guard.ps1 -Uninstall   undo everything and start Ollama

  -Install adds the Windows task "Ollama trading-hours guard (2026-11-12)"
  (Mon-Fri 09:30 and 16:15; runs late if the laptop was asleep or off) and puts
  a guard shortcut in the Startup folder in place of Ollama's own (the original
  is kept next to this file for -Uninstall).
  Log: %LOCALAPPDATA%\ollama_guard.log
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Delay = 0
)

$ErrorActionPreference = 'Stop'

$TaskName   = 'Ollama trading-hours guard (2026-11-12)'
$QuietFrom  = New-TimeSpan -Hours 9 -Minutes 30
$QuietUntil = New-TimeSpan -Hours 16 -Minutes 15
$Startup    = [Environment]::GetFolderPath('Startup')
$OllamaLnk  = Join-Path $Startup 'Ollama.lnk'
$GuardLnk   = Join-Path $Startup 'Ollama guard (2026-11-12).lnk'
$BackupLnk  = Join-Path $PSScriptRoot 'Ollama_startup_backup_20261112.lnk'
$LogFile    = Join-Path $env:LOCALAPPDATA 'ollama_guard.log'

function Write-GuardLog([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    Add-Content -Path $LogFile -Value $line
    Write-Output $line
}

function Get-OllamaLauncher {
    # Prefer Ollama's own Startup shortcut: same start as at log-in, so its
    # settings (e.g. "Expose to network") apply. Else the app itself.
    foreach ($p in @($OllamaLnk, $BackupLnk)) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $candidates = @(Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe')
    $cli = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cli) { $candidates += Join-Path (Split-Path -Parent $cli.Source) 'ollama app.exe' }
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

function Get-OllamaProcess {
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like 'ollama*' }
}

function Test-TradingHours([datetime]$Now) {
    $weekday = ($Now.DayOfWeek -ne [DayOfWeek]::Saturday) -and ($Now.DayOfWeek -ne [DayOfWeek]::Sunday)
    return ($weekday -and ($Now.TimeOfDay -ge $QuietFrom) -and ($Now.TimeOfDay -lt $QuietUntil))
}

function Invoke-Guard {
    $procs = @(Get-OllamaProcess)
    if (Test-TradingHours (Get-Date)) {
        if ($procs.Count -gt 0) {
            $procs | Stop-Process -Force -ErrorAction SilentlyContinue
            Write-GuardLog 'trading hours: Ollama closed'
        } else {
            Write-GuardLog 'trading hours: Ollama already closed'
        }
        return
    }
    if ($procs.Count -gt 0) {
        Write-GuardLog 'outside trading hours: Ollama already running'
        return
    }
    $launcher = Get-OllamaLauncher
    if (-not $launcher) {
        Write-GuardLog 'outside trading hours: Ollama NOT FOUND - nothing started'
        return
    }
    Start-Process -FilePath $launcher
    Write-GuardLog ('outside trading hours: Ollama started (' + $launcher + ')')
}

function Install-Guard {
    $launcher = Get-OllamaLauncher
    if (-not $launcher) {
        throw ('Ollama not found (no Startup shortcut, no ' + $env:LOCALAPPDATA + '\Programs\Ollama\ollama app.exe). Nothing changed.')
    }
    $guardArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
    $me = $env:USERDOMAIN + '\' + $env:USERNAME
    $days = @('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday')
    $triggers = @(
        (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '09:30'),
        (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At '16:15')
    )
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $guardArgs
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    $desc = 'trading-intel 2026-11-13: Ollama closed Mon-Fri 09:30-16:15, open otherwise (08:00-09:30 = morning Doc read only). Undo: ollama_guard.ps1 -Uninstall'
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description $desc -Force | Out-Null

    # Log-in: the guard shortcut replaces Ollama's own (kept here for -Uninstall).
    # The 60 s wait lets any other auto-start of Ollama happen first.
    if (Test-Path -LiteralPath $OllamaLnk) {
        Move-Item -LiteralPath $OllamaLnk -Destination $BackupLnk -Force
    }
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($GuardLnk)
    $sc.TargetPath = 'powershell.exe'
    $sc.Arguments = $guardArgs + ' -Delay 60'
    $sc.WindowStyle = 7
    $sc.Description = $TaskName
    $sc.Save()
    Write-GuardLog ('installed: task "' + $TaskName + '" (Mon-Fri 09:30 + 16:15) and log-in shortcut; Ollama starts via ' + (Get-OllamaLauncher))
    Invoke-Guard
}

function Uninstall-Guard {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $GuardLnk) { Remove-Item -LiteralPath $GuardLnk -Force }
    if (Test-Path -LiteralPath $BackupLnk) { Move-Item -LiteralPath $BackupLnk -Destination $OllamaLnk -Force }
    Write-GuardLog 'uninstalled: task and log-in shortcut removed, Ollama Startup shortcut restored'
    if (@(Get-OllamaProcess).Count -eq 0) {
        $launcher = Get-OllamaLauncher
        if ($launcher) {
            Start-Process -FilePath $launcher
            Write-GuardLog 'Ollama started'
        }
    }
}

try {
    if ($Install) {
        Install-Guard
    } elseif ($Uninstall) {
        Uninstall-Guard
    } else {
        if ($Delay -gt 0) { Start-Sleep -Seconds $Delay }
        Invoke-Guard
    }
} catch {
    Write-GuardLog ('ERROR: ' + $_.Exception.Message)
    exit 1
}
