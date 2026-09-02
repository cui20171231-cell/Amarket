param(
    [Parameter(Mandatory = $true)]
    [string]$BackupDirectory,
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$Distribution = 'Ubuntu',
    [switch]$ConfirmServicePause
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmServicePause) {
    throw 'This backup pauses ClickHouse briefly. Run again with -ConfirmServicePause after confirming the collector is idle.'
}

$resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$resolvedBackup = (Resolve-Path -LiteralPath $BackupDirectory).Path
$projectDrive = [IO.Path]::GetPathRoot($resolvedProject).TrimEnd('\')
$backupDrive = [IO.Path]::GetPathRoot($resolvedBackup).TrimEnd('\')
if ([string]::IsNullOrWhiteSpace($backupDrive) -or $backupDrive -eq $projectDrive) {
    throw 'BackupDirectory must be on a different drive from the A-market repository.'
}
if ($resolvedBackup -notmatch '^([A-Za-z]):\\(.*)$') {
    throw 'BackupDirectory must be a Windows drive-letter path that WSL can access, for example E:\Amarket-backups.'
}

$drive = $Matches[1].ToLowerInvariant()
$tail = $Matches[2].Replace('\', '/')
$linuxBackupDirectory = "/mnt/$drive/$tail"
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$fileName = "amarket-clickhouse-$stamp.tgz"
$windowsBackupFile = Join-Path $resolvedBackup $fileName
$linuxBackupFile = "$linuxBackupDirectory/$fileName"
$containerStopped = $false

try {
    & wsl.exe -d $Distribution --user root -- docker stop amarket-clickhouse | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not stop the ClickHouse container.'
    }
    $containerStopped = $true
    & wsl.exe -d $Distribution --user root -- tar `
        --numeric-owner -C /var/lib/amarket -czf $linuxBackupFile clickhouse
    if ($LASTEXITCODE -ne 0) {
        throw 'Creating the ClickHouse backup archive failed.'
    }
} finally {
    if ($containerStopped) {
        & wsl.exe -d $Distribution --user root -- docker start amarket-clickhouse | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $windowsBackupFile)) {
    throw 'The backup archive was not created at the requested destination.'
}
& wsl.exe -d $Distribution --user root -- tar -tzf $linuxBackupFile | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'The backup archive failed its integrity check.'
}
$hash = (Get-FileHash -LiteralPath $windowsBackupFile -Algorithm SHA256).Hash
$hashPath = "$windowsBackupFile.sha256"
[IO.File]::WriteAllText(
    $hashPath,
    "$hash  $fileName`r`n",
    (New-Object Text.UTF8Encoding($false))
)

Get-Item -LiteralPath $windowsBackupFile |
    Select-Object FullName, Length, LastWriteTime
Write-Output "SHA-256: $hash"
Write-Output "Checksum file: $hashPath"
