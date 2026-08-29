$ErrorActionPreference = 'Stop'

$projectRoot = 'D:\Amarket'
. (Join-Path $projectRoot 'scripts\ensure_clickhouse.ps1')
Start-AmarketClickHouse -ProjectRoot $projectRoot

& py -3.11 -m app.hithink.cli daily-auto
exit $LASTEXITCODE
