$ErrorActionPreference = "Stop"

function New-ProbeFailure {
    param([string]$Text)

    return [pscustomobject]@{
        overall_health = "Unknown"
        tooltip = $Text
        clickhouse = [pscustomobject]@{
            normal = $false
            text = "ClickHouse：状态未知"
        }
    }
}

function Invoke-CollectionProbe {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "C:\Program Files\Python311\python.exe"
    $startInfo.Arguments = "-m app.hithink.tray_probe"
    $startInfo.WorkingDirectory = "D:\Amarket"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::Start($startInfo)
    try {
        if (-not $process.WaitForExit(6000)) {
            $process.Kill()
            [void]$process.WaitForExit(1000)
            return New-ProbeFailure -Text "个股快照行情：状态读取超时"
        }

        $raw = $process.StandardOutput.ReadToEnd() -split "`r?`n" |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -Last 1
        if ([string]::IsNullOrWhiteSpace([string]$raw)) {
            return New-ProbeFailure -Text "个股快照行情：状态读取失败"
        }

        return [string]$raw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        return New-ProbeFailure -Text "个股快照行情：状态读取失败"
    }
    finally {
        $process.Dispose()
    }
}

function Get-TaskSnapshot {
    param([string[]]$TaskNames)

    $result = [ordered]@{}
    $service = New-Object -ComObject "Schedule.Service"
    $service.Connect()
    $folder = $service.GetFolder("\")

    foreach ($taskName in $TaskNames) {
        try {
            $task = $folder.GetTask("\$taskName")
            $state = switch ([int]$task.State) {
                1 { "Disabled" }
                2 { "Queued" }
                3 { "Ready" }
                4 { "Running" }
                default { "Unknown" }
            }
            $lastRunTime = if ($task.LastRunTime.Year -ge 2000) {
                $task.LastRunTime.ToString("o")
            }
            else {
                $null
            }
            $result[$taskName] = [ordered]@{
                exists = $true
                state = $state
                running = $state -eq "Running"
                lastTaskResult = [int64]$task.LastTaskResult
                lastRunTime = $lastRunTime
            }
        }
        catch {
            $result[$taskName] = [ordered]@{
                exists = $false
                state = "Missing"
                running = $false
                lastTaskResult = $null
                lastRunTime = $null
            }
        }
    }

    return [pscustomobject]$result
}

function Get-ProcessSnapshot {
    $collector = $null
    $gateway = $null
    $tunnel = $null
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Sort-Object CreationDate

    # After a machine restart these services can run elevated, so their command
    # lines may be hidden from the non-elevated tray process.  Bind detection to
    # the real local service ports and verify the owning executable instead.
    $gatewayListener = Get-NetTCPConnection `
        -LocalAddress "127.0.0.1" `
        -LocalPort 2091 `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $tunnelListener = Get-NetTCPConnection `
        -LocalAddress "127.0.0.1" `
        -LocalPort 8080 `
        -State Listen `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1

    foreach ($process in $processes) {
        $commandLine = [string]$process.CommandLine
        if (($null -eq $collector) -and
            ($commandLine -match "app\.hithink\.cli\s+serve")) {
            $collector = $process
        }
        if (($null -eq $gateway) -and
            ($null -ne $gatewayListener) -and
            ([int64]$process.ProcessId -eq [int64]$gatewayListener.OwningProcess) -and
            ($process.Name -in @("python.exe", "pythonw.exe"))) {
            $gateway = $process
        }
        if (($null -eq $tunnel) -and
            ($process.Name -eq "tunnel-client.exe") -and
            ($null -ne $tunnelListener) -and
            ([int64]$process.ProcessId -eq [int64]$tunnelListener.OwningProcess)) {
            $tunnel = $process
        }
    }

    $gatewayRunning = ($null -ne $gateway) -and
        ($null -ne $gatewayListener) -and
        ([int64]$gatewayListener.OwningProcess -eq [int64]$gateway.ProcessId)
    $tunnelRunning = ($null -ne $tunnel) -and
        ($null -ne $tunnelListener) -and
        ([int64]$tunnelListener.OwningProcess -eq [int64]$tunnel.ProcessId)

    return [pscustomobject]@{
        collector = [pscustomobject]@{
            running = $null -ne $collector
            startedAt = if ($null -ne $collector) {
                $collector.CreationDate.ToString("o")
            }
            else {
                $null
            }
        }
        gateway = [pscustomobject]@{
            running = $gatewayRunning
            startedAt = if ($gatewayRunning) {
                $gateway.CreationDate.ToString("o")
            }
            else {
                $null
            }
        }
        tunnel = [pscustomobject]@{
            running = $tunnelRunning
            startedAt = if ($tunnelRunning) {
                $tunnel.CreationDate.ToString("o")
            }
            else {
                $null
            }
        }
    }
}

$taskNames = @(
    "HithinkSnapshotCollector",
    "MarketOS-Gateway",
    "MarketOS-Tunnel"
)

try {
    $tasks = Get-TaskSnapshot -TaskNames $taskNames
}
catch {
    $tasks = [pscustomobject]@{}
}

try {
    $processes = Get-ProcessSnapshot
}
catch {
    $processes = [pscustomobject]@{}
}

$result = [pscustomobject]@{
    probe = Invoke-CollectionProbe
    tasks = $tasks
    processes = $processes
}

$result | ConvertTo-Json -Depth 8 -Compress
