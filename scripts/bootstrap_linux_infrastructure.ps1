param(
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$Distribution = 'Ubuntu'
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell window.'
}

$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
if ($resolvedRoot -ne 'D:\Amarket') {
    throw 'The Linux watchdog and log-maintenance units currently require the repository at D:\Amarket.'
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedRoot 'secrets\collector.env'))) {
    throw 'Missing secrets\collector.env. Run scripts\configure_collector_credentials.ps1 first.'
}

$distributionNames = & wsl.exe --list --quiet 2>$null
if ($LASTEXITCODE -ne 0 -or $distributionNames -notcontains $Distribution) {
    throw "WSL distribution '$Distribution' is not installed. Install WSL and Ubuntu before continuing."
}

$linuxRoot = '/mnt/d/Amarket'
$dockerProbe = & wsl.exe -d $Distribution --user root -- bash -lc 'command -v docker >/dev/null 2>&1'
if ($LASTEXITCODE -ne 0) {
    & wsl.exe -d $Distribution --user root -- bash "$linuxRoot/scripts/linux/install_docker_engine.sh"
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Engine installation inside Ubuntu failed.'
    }
}

& wsl.exe -d $Distribution --user root -- install -m 0755 `
    "$linuxRoot/scripts/linux/amarket_wsl_host.sh" '/usr/local/sbin/amarket-wsl-host'
if ($LASTEXITCODE -ne 0) {
    throw 'Installing the Linux watchdog failed.'
}
& wsl.exe -d $Distribution --user root -- mkdir -p '/var/lib/amarket/clickhouse'
& wsl.exe -d $Distribution --user root -- bash -lc `
    "cd '$linuxRoot' && docker compose -f compose.linux.yaml up -d"
if ($LASTEXITCODE -ne 0) {
    throw 'Starting the A-market ClickHouse container failed.'
}
& wsl.exe -d $Distribution --user root -- bash "$linuxRoot/infra/linux/install_log_maintenance.sh"
if ($LASTEXITCODE -ne 0) {
    throw 'Installing Linux log maintenance failed.'
}

& (Join-Path $resolvedRoot 'scripts\install_linux_infrastructure_task.ps1') `
    -ProjectRoot $resolvedRoot `
    -WindowsUser $identity.Name | Out-Null

$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 2
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8123/ping' -TimeoutSec 3
        $ready = $response.StatusCode -eq 200
    } catch {
        $ready = $false
    }
} while (-not $ready -and (Get-Date) -lt $deadline)
if (-not $ready) {
    throw 'ClickHouse did not become ready on 127.0.0.1:8123 within 60 seconds.'
}

Write-Output 'Linux Docker, ClickHouse, watchdog, log maintenance, and Windows startup task are ready.'
