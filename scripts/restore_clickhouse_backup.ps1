param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$Distribution = 'Ubuntu',
    [switch]$ConfirmReplaceCurrentData
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmReplaceCurrentData) {
    throw 'Restoring replaces the current ClickHouse data directory. Run again with -ConfirmReplaceCurrentData.'
}

$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
if ($resolvedBackup -notmatch '^([A-Za-z]):\\(.*)$') {
    throw 'BackupFile must be a Windows drive-letter path that WSL can access.'
}
$drive = $Matches[1].ToLowerInvariant()
$tail = $Matches[2].Replace('\', '/')
$linuxBackup = "/mnt/$drive/$tail"

$hashPath = "$resolvedBackup.sha256"
if (Test-Path -LiteralPath $hashPath) {
    $expectedHash = ((Get-Content -LiteralPath $hashPath -Raw).Trim() -split '\s+')[0]
    $actualHash = (Get-FileHash -LiteralPath $resolvedBackup -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw 'Backup SHA-256 check failed. The archive may be incomplete or damaged.'
    }
}

& wsl.exe -d $Distribution --user root -- tar -tzf $linuxBackup | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'The backup archive failed its integrity check.'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$staging = "/var/lib/amarket/restore-$stamp"
$previous = "/var/lib/amarket/clickhouse.before-restore-$stamp"
& wsl.exe -d $Distribution --user root -- mkdir -p $staging
& wsl.exe -d $Distribution --user root -- tar --numeric-owner -xzf $linuxBackup -C $staging
if ($LASTEXITCODE -ne 0) {
    throw 'Extracting the backup into a staging directory failed.'
}
& wsl.exe -d $Distribution --user root -- test -d "$staging/clickhouse"
if ($LASTEXITCODE -ne 0) {
    throw 'The archive does not contain the expected clickhouse data directory.'
}

& wsl.exe -d $Distribution --user root -- docker stop amarket-clickhouse | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not stop the ClickHouse container.'
}
$swapped = $false
try {
    & wsl.exe -d $Distribution --user root -- mv /var/lib/amarket/clickhouse $previous
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not preserve the current ClickHouse directory.'
    }
    & wsl.exe -d $Distribution --user root -- mv "$staging/clickhouse" /var/lib/amarket/clickhouse
    if ($LASTEXITCODE -ne 0) {
        & wsl.exe -d $Distribution --user root -- mv $previous /var/lib/amarket/clickhouse
        throw 'Could not activate the restored ClickHouse directory.'
    }
    $swapped = $true
    & wsl.exe -d $Distribution --user root -- docker start amarket-clickhouse | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'The restored ClickHouse container did not start.'
    }
} catch {
    if ($swapped) {
        & wsl.exe -d $Distribution --user root -- docker stop amarket-clickhouse | Out-Null
        & wsl.exe -d $Distribution --user root -- mv /var/lib/amarket/clickhouse "$staging/failed-clickhouse"
        & wsl.exe -d $Distribution --user root -- mv $previous /var/lib/amarket/clickhouse
        & wsl.exe -d $Distribution --user root -- docker start amarket-clickhouse | Out-Null
    }
    throw
}

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
    throw "The restored database did not become healthy. The previous data remains at $previous."
}

Write-Output 'ClickHouse backup restored and the database is healthy.'
Write-Output "The pre-restore data remains at $previous until you explicitly remove it."
