$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Amarket'
$beijingZone = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$beijingNow = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $beijingZone)
if ($beijingNow.TimeOfDay -lt [TimeSpan]::FromHours(16)) {
    Write-Output "Daily-K task ignored before 16:00 Beijing time."
    exit 0
}

. (Join-Path $projectRoot 'scripts\ensure_clickhouse.ps1')
Start-AmarketClickHouse -ProjectRoot $projectRoot

& py -3.11 -m app.hithink.cli daily-auto
exit $LASTEXITCODE
