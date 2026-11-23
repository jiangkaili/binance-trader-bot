#requires -version 5
<#
.SYNOPSIS
    Idempotent supervisor for live_trader.py on Windows.

.DESCRIPTION
    Starts the trading bot only if it is not already running, then exits.
    Designed to be called repeatedly by a Scheduled Task (every few minutes):
    if the bot is alive this does nothing; if it died, it comes back.

    Why this exists: the repo's scripts/trade_watchdog.py shells out to
    `pgrep`, which does not exist on Windows, and nothing else restarts the
    process. Running the bot in a foreground terminal means it dies with that
    terminal (and with the agent session that spawned it).

    What it does when starting:
      1. ipconfig /flushdns — the ISP DNS intermittently returns a black-holed
         address for fapi.binance.com, which looks like a network outage.
      2. Launches .venv\Scripts\python.exe scripts\live_trader.py hidden,
         detached, with stdout/stderr appended to data\trader_stdout.log.
      3. Appends one line to data\supervisor.log.

.PARAMETER Status
    Print state and exit. Never starts anything.

.PARAMETER Restart
    Stop the running bot (graceful SIGTERM equivalent) then start a fresh one.

.EXAMPLE
    pwsh -NoProfile -File scripts\win\supervise_trader.ps1
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$TraderScript = Join-Path $ProjectRoot 'scripts\live_trader.py'
$DataDir = Join-Path $ProjectRoot 'data'
$EnvFile = Join-Path $ProjectRoot '.env'
$StdoutLog = Join-Path $DataDir 'trader_stdout.log'
$StderrLog = Join-Path $DataDir 'trader_stderr.log'
$SupervisorLog = Join-Path $DataDir 'supervisor.log'
$LockFile = Join-Path $DataDir '.supervisor.lock'

function Write-SupLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'), $Message
    try {
        if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }
        Add-Content -Path $SupervisorLog -Value $line -Encoding utf8
    } catch { }
    Write-Host $line
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'live_trader\.py' }
}

$botProcs = @(Get-BotProcesses)

if ($Status) {
    if ($botProcs.Count -eq 0) {
        Write-Host "bot: NOT running"
    } else {
        Write-Host "bot: running (PIDs: $($botProcs.ProcessId -join ', '))"
        foreach ($p in $botProcs) { Write-Host ("  pid {0}  started {1}" -f $p.ProcessId, $p.CreationDate) }
    }
    $statePath = Join-Path $DataDir 'live_trader.state'
    if (Test-Path $statePath) {
        $age = ((Get-Date) - (Get-Item $statePath).LastWriteTime).TotalSeconds
        Write-Host ("state age: {0:N0}s" -f $age)
    } else {
        Write-Host "state: missing"
    }
    exit 0
}

if ($Restart) {
    foreach ($p in $botProcs) {
        Write-SupLog "restart requested - stopping pid $($p.ProcessId)"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
    $botProcs = @(Get-BotProcesses)
}

if ($botProcs.Count -gt 0) {
    # Already alive: stay quiet so the scheduled task log does not fill up.
    Write-Host "bot already running (PID $($botProcs.ProcessId -join ',')) - nothing to do"
    exit 0
}

# ---- single-instance guard -------------------------------------------------
$acquired = $false
try {
    $fs = [System.IO.File]::Open($LockFile, 'CreateNew', 'Write', 'None')
    $fs.Write([System.Text.Encoding]::UTF8.GetBytes("$PID"), 0, "$PID".Length)
    $fs.Close()
    $acquired = $true
} catch {
    $stale = $false
    if (Test-Path $LockFile) {
        $age = ((Get-Date) - (Get-Item $LockFile).LastWriteTime).TotalSeconds
        if ($age -gt 120) { $stale = $true }
    }
    if ($stale) {
        Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
        try {
            $fs = [System.IO.File]::Open($LockFile, 'CreateNew', 'Write', 'None'); $fs.Close(); $acquired = $true
        } catch { }
    }
    if (-not $acquired) {
        Write-Host "another supervisor is starting the bot - skipping"
        exit 0
    }
}

try {
    if (-not (Test-Path $VenvPython)) {
        Write-SupLog "FATAL: venv python missing at $VenvPython"
        exit 1
    }
    if (-not (Test-Path $TraderScript)) {
        Write-SupLog "FATAL: trader script missing at $TraderScript"
        exit 1
    }
    if (-not (Test-Path $EnvFile)) {
        Write-SupLog "FATAL: .env missing at $EnvFile"
        exit 1
    }

    # DNS pollution guard: a poisoned answer for fapi.binance.com looks exactly
    # like the exchange being down. Flushing costs ~100ms and fixes it.
    try { & ipconfig /flushdns 2>&1 | Out-Null } catch { }

    # The bot reads .env itself; make sure no stale proxy env var is inherited,
    # otherwise a stopped VPN turns every request into a ProxyError.
    Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY -ErrorAction SilentlyContinue

    $argList = @(
        $TraderScript,
        '--env-file', $EnvFile
    )

    $startInfo = @{
        FilePath               = $VenvPython
        ArgumentList           = $argList
        WorkingDirectory       = $ProjectRoot
        WindowStyle            = 'Hidden'
        RedirectStandardOutput = $StdoutLog
        RedirectStandardError  = $StderrLog
        PassThru               = $true
    }
    $proc = Start-Process @startInfo
    Write-SupLog "started live_trader.py (pid $($proc.Id)) -> $StdoutLog"

    Start-Sleep -Seconds 8
    $alive = @(Get-BotProcesses)
    if ($alive.Count -eq 0) {
        Write-SupLog "WARNING: bot exited within 8s - check $StdoutLog"
        exit 1
    }
    Write-SupLog "ok: bot alive (pid $($alive.ProcessId -join ','))"
    exit 0
} finally {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}
