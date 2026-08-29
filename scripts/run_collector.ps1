$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Amarket'

# Task Scheduler can terminate this PowerShell wrapper without terminating its
# Python child.  Refuse a second wrapper whenever the collector is already
# running, so a manual restart can never create duplicate collectors.
$existingCollector = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'python.exe' -and $_.CommandLine -match 'app\.hithink\.cli serve'
}
if ($existingCollector) {
    exit 0
}

. (Join-Path $projectRoot 'scripts\ensure_clickhouse.ps1')
Start-AmarketClickHouse -ProjectRoot $projectRoot

& py -3.11 -m app.hithink.cli serve
exit $LASTEXITCODE
