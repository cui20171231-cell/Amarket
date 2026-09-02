param(
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$ClickHouseBackupFile = '',
    [switch]$InstallGatewayAndTunnel
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell window.'
}

$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\')
if ($resolvedRoot -ne 'D:\Amarket') {
    throw 'The current production configuration requires the repository at D:\Amarket.'
}
if (-not (Test-Path -LiteralPath (Join-Path $resolvedRoot 'secrets\collector.env'))) {
    throw 'Missing collector credentials. Run scripts\configure_collector_credentials.ps1 first.'
}

& py.exe -3.11 --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 is required and was not found by the Windows Python launcher.'
}
& py.exe -3.11 -m pip install -r (Join-Path $resolvedRoot 'requirements-recovery.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Installing the tested Python dependency versions failed.'
}
& py.exe -3.11 -m pip install --no-deps -e $resolvedRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Installing the A-market Python package failed.'
}

& (Join-Path $resolvedRoot 'scripts\bootstrap_linux_infrastructure.ps1') -ProjectRoot $resolvedRoot
if (-not [string]::IsNullOrWhiteSpace($ClickHouseBackupFile)) {
    & (Join-Path $resolvedRoot 'scripts\restore_clickhouse_backup.ps1') `
        -BackupFile $ClickHouseBackupFile `
        -ConfirmReplaceCurrentData
}

Push-Location $resolvedRoot
try {
    & py.exe -3.11 -m app.hithink.cli init-db
    if ($LASTEXITCODE -ne 0) {
        throw 'Creating or validating the ClickHouse tables failed.'
    }
} finally {
    Pop-Location
}

& (Join-Path $resolvedRoot 'scripts\install_collector_task.ps1') -ProjectRoot $resolvedRoot | Out-Null
& (Join-Path $resolvedRoot 'scripts\install_tray_task.ps1') -ProjectRoot $resolvedRoot | Out-Null

if ($InstallGatewayAndTunnel) {
    $tunnelId = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_TUNNEL_ID', 'Machine')
    $tunnelKey = [Environment]::GetEnvironmentVariable('CONTROL_PLANE_API_KEY', 'Machine')
    if ([string]::IsNullOrWhiteSpace($tunnelId) -or [string]::IsNullOrWhiteSpace($tunnelKey)) {
        throw 'Tunnel credentials are missing. Run scripts\configure_tunnel_credentials.ps1 first.'
    }
    & cmd.exe /d /c (Join-Path $resolvedRoot 'INSTALL_AI_GATEWAY.cmd')
    if ($LASTEXITCODE -ne 0) {
        throw 'Installing the AI gateway environment failed.'
    }
    & (Join-Path $resolvedRoot 'scripts\install_gateway_tasks.ps1') -ProjectRoot $resolvedRoot | Out-Null
}

Write-Output 'A-market core infrastructure, database schema, collector, and tray task were restored.'
Write-Output 'Email and Feishu credentials are machine-specific and must be configured separately if used.'
