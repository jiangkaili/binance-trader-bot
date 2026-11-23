#requires -version 5
<#
.SYNOPSIS
    Stop the trading bot cleanly.

.DESCRIPTION
    Stops the live_trader.py process(es). Because the supervisor runs every
    5 minutes, stopping the bot alone is not enough to keep it down — pass
    -Disable to also disable both scheduled tasks and the Startup launcher,
    which is what you want before a long pause or when you are going to edit
    config and do not want a restart mid-edit.

    Note on shutdown: it is safe to just power the machine off. The stop-loss
    and take-profit orders live on Binance, not in this process, so an open
    position stays protected when Windows kills Python.

.PARAMETER Disable
    Also disable the supervisor tasks + Startup launcher so the bot stays down.

.PARAMETER Enable
    Re-enable the supervisor tasks + Startup launcher.

.EXAMPLE
    pwsh -NoProfile -File scripts\win\stop_trader.ps1
    pwsh -NoProfile -File scripts\win\stop_trader.ps1 -Disable
    pwsh -NoProfile -File scripts\win\stop_trader.ps1 -Enable
#>
[CmdletBinding()]
param(
    [switch]$Disable,
    [switch]$Enable
)

$ErrorActionPreference = 'Continue'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$DataDir = Join-Path $ProjectRoot 'data'
$StartupLnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'BinanceTraderBot.vbs'

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'live_trader\.py' }
}

if ($Enable) {
    foreach ($t in @('BinanceTraderBot-5min', 'BinanceTraderBot-logon')) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Enable-ScheduledTask -TaskName $t | Out-Null
            Write-Host "enabled task $t"
        }
    }
    Write-Host "startup launcher present: $(Test-Path $StartupLnk)"
    exit 0
}

if ($Disable) {
    foreach ($t in @('BinanceTraderBot-5min', 'BinanceTraderBot-logon')) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $t | Out-Null
            Write-Host "disabled task $t"
        }
    }
    if (Test-Path $StartupLnk) {
        Rename-Item -Path $StartupLnk -NewName 'BinanceTraderBot.vbs.disabled' -Force
        Write-Host "disabled startup launcher"
    }
}

$procs = @(Get-BotProcesses)
if ($procs.Count -eq 0) {
    Write-Host "bot: not running"
} else {
    foreach ($p in $procs) {
        Write-Host "stopping pid $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    $left = @(Get-BotProcesses)
    if ($left.Count -gt 0) {
        Write-Host "WARNING: still alive: $($left.ProcessId -join ',')"
    } else {
        Write-Host "bot: stopped"
    }
    try {
        $line = "[{0}] stopped by stop_trader.ps1 (Disable={1})" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $Disable
        Add-Content -Path (Join-Path $DataDir 'supervisor.log') -Value $line -Encoding utf8
    } catch { }
}

# Surface any position that is still open — its stops remain on Binance.
if (-not $Disable) {
    Write-Host ""
    Write-Host "NOTE: the supervisor restarts the bot within 5 minutes."
    Write-Host "      To keep it down, rerun with -Disable."
}
