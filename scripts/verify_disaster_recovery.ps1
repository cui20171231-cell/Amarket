param(
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$OffMachineBackupPath = ''
)

$ErrorActionPreference = 'SilentlyContinue'
$results = [Collections.Generic.List[object]]::new()

function Add-Result([string]$Item, [string]$Status, [string]$Detail) {
    $results.Add([pscustomobject]@{
        Item = $Item
        Status = $Status
        Detail = $Detail
    })
}

$requiredFiles = @(
    'README.md',
    'ARCHITECTURE.md',
    'docs\DISASTER_RECOVERY.md',
    'requirements-recovery.txt',
    'compose.linux.yaml',
    'sql\hithink_snapshot.sql',
    'scripts\restore_amarket_host.ps1',
    'scripts\backup_clickhouse_off_machine.ps1',
    'scripts\restore_clickhouse_backup.ps1',
    'scripts\install_collector_task.ps1',
    'scripts\install_linux_infrastructure_task.ps1',
    'scripts\install_tray_task.ps1'
)
$missingFiles = @($requiredFiles | Where-Object {
    -not (Test-Path -LiteralPath (Join-Path $ProjectRoot $_))
})
if ($missingFiles.Count -eq 0) {
    Add-Result 'Recovery files' 'PASS' 'All required tracked files exist.'
} else {
    Add-Result 'Recovery files' 'FAIL' ("Missing: " + ($missingFiles -join ', '))
}

$remote = (& git.exe -C $ProjectRoot remote get-url Amarket 2>$null | Select-Object -First 1)
if (-not [string]::IsNullOrWhiteSpace([string]$remote)) {
    Add-Result 'Git remote' 'PASS' $remote
} else {
    Add-Result 'Git remote' 'FAIL' 'Remote named Amarket is not configured.'
}
$headHash = (& git.exe -C $ProjectRoot rev-parse HEAD 2>$null | Select-Object -First 1)
$upstreamHash = (& git.exe -C $ProjectRoot rev-parse '@{u}' 2>$null | Select-Object -First 1)
if (-not [string]::IsNullOrWhiteSpace([string]$headHash) -and $headHash -eq $upstreamHash) {
    Add-Result 'Git upload' 'PASS' 'Local HEAD matches its tracked remote branch.'
} else {
    Add-Result 'Git upload' 'WARN' 'Local HEAD does not match its tracked remote branch.'
}
$gitStatus = @(& git.exe -C $ProjectRoot status --porcelain 2>$null)
if ($LASTEXITCODE -ne 0) {
    Add-Result 'Git worktree' 'FAIL' 'Git status could not be read.'
} elseif ($gitStatus.Count -eq 0) {
    Add-Result 'Git worktree' 'PASS' 'No local-only changes.'
} else {
    Add-Result 'Git worktree' 'WARN' "$($gitStatus.Count) local change(s) are not yet represented by HEAD."
}

& py.exe -3.11 --version 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Add-Result 'Python 3.11' 'PASS' 'Windows Python launcher found Python 3.11.'
} else {
    Add-Result 'Python 3.11' 'FAIL' 'Python 3.11 is unavailable.'
}

$secretPath = Join-Path $ProjectRoot 'secrets\collector.env'
$requiredSecretKeys = @('HITHINK_FINANCE_API_KEY', 'CLICKHOUSE_PASSWORD')
if (Test-Path -LiteralPath $secretPath) {
    $configuredKeys = @{}
    foreach ($line in Get-Content -LiteralPath $secretPath) {
        if ($line -match '^([^#=]+)=(.*)$') {
            $configuredKeys[$Matches[1].Trim()] = -not [string]::IsNullOrWhiteSpace($Matches[2])
        }
    }
    $missingKeys = @($requiredSecretKeys | Where-Object { -not $configuredKeys[$_] })
    if ($missingKeys.Count -eq 0) {
        Add-Result 'Collector credentials' 'PASS' 'Required values exist; values were not displayed.'
    } else {
        Add-Result 'Collector credentials' 'FAIL' ("Missing or empty: " + ($missingKeys -join ', '))
    }
} else {
    Add-Result 'Collector credentials' 'FAIL' 'secrets\collector.env is missing.'
}

& wsl.exe -d Ubuntu --user root -- docker inspect amarket-clickhouse 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) {
    Add-Result 'ClickHouse container' 'PASS' 'amarket-clickhouse exists in Ubuntu.'
} else {
    Add-Result 'ClickHouse container' 'FAIL' 'amarket-clickhouse was not found in Ubuntu.'
}
try {
    $ping = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8123/ping' -TimeoutSec 3
    if ($ping.StatusCode -eq 200) {
        Add-Result 'ClickHouse health' 'PASS' '127.0.0.1:8123 responded.'
    } else {
        Add-Result 'ClickHouse health' 'FAIL' "HTTP status $($ping.StatusCode)."
    }
} catch {
    Add-Result 'ClickHouse health' 'FAIL' '127.0.0.1:8123 did not respond.'
}

foreach ($taskName in @('AmarketLinuxInfrastructure', 'HithinkSnapshotCollector')) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        Add-Result "Task $taskName" 'FAIL' 'Task is not installed.'
    } elseif ($task.State -in @('Running', 'Ready')) {
        Add-Result "Task $taskName" 'PASS' "State: $($task.State)."
    } else {
        Add-Result "Task $taskName" 'WARN' "State: $($task.State)."
    }
}
$trayTask = Get-ScheduledTask -TaskName 'MarketOSStatus-TrayIcons' -ErrorAction SilentlyContinue
if ($null -eq $trayTask) {
    Add-Result 'Tray task' 'WARN' 'MarketOSStatus-TrayIcons is not installed.'
} else {
    Add-Result 'Tray task' 'PASS' "State: $($trayTask.State)."
}

if ([string]::IsNullOrWhiteSpace($OffMachineBackupPath)) {
    Add-Result 'Off-machine database backup' 'WARN' 'No backup path was supplied for verification.'
} elseif (-not (Test-Path -LiteralPath $OffMachineBackupPath)) {
    Add-Result 'Off-machine database backup' 'FAIL' 'The supplied backup file does not exist.'
} else {
    $resolvedProject = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $resolvedBackup = (Resolve-Path -LiteralPath $OffMachineBackupPath).Path
    if ([IO.Path]::GetPathRoot($resolvedProject) -eq [IO.Path]::GetPathRoot($resolvedBackup)) {
        Add-Result 'Off-machine database backup' 'FAIL' 'The backup is on the same drive as the project.'
    } else {
        Add-Result 'Off-machine database backup' 'PASS' $resolvedBackup
    }
}

$results | Format-Table -AutoSize -Wrap
if (@($results | Where-Object Status -eq 'FAIL').Count -gt 0) {
    exit 1
}
