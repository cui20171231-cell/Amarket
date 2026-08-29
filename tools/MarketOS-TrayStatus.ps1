Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class MarketOSNativeIconV3
{
    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@

[System.Windows.Forms.Application]::EnableVisualStyles()

$stateRegistryPath = "HKCU:\Software\MarketOS\TrayStatus"
if (-not (Test-Path $stateRegistryPath)) {
    New-Item -Path $stateRegistryPath -Force | Out-Null
}

function New-ShapeIcon {
    param(
        [ValidateSet("Circle", "Square", "Triangle")]
        [string]$Shape,

        [ValidateSet("Normal", "Partial", "Abnormal", "Unknown")]
        [string]$Status
    )

    $bitmap = [System.Drawing.Bitmap]::new(32, 32)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias

    $outline = [System.Drawing.Pen]::new([System.Drawing.Color]::Black, 2.4)
    $brush = $null

    if (($Status -eq "Normal") -or ($Status -eq "Partial")) {
        $brush = [System.Drawing.SolidBrush]::new(
            [System.Drawing.Color]::FromArgb(34, 197, 94)
        )
    }
    elseif ($Status -eq "Abnormal") {
        $brush = [System.Drawing.SolidBrush]::new(
            [System.Drawing.Color]::FromArgb(239, 68, 68)
        )
    }

    $triangle = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new(16, 3),
        [System.Drawing.PointF]::new(29, 27),
        [System.Drawing.PointF]::new(3, 27)
    )

    if (($null -ne $brush) -and ($Status -eq "Partial")) {
        $clip = [System.Drawing.Region]::new(
            [System.Drawing.RectangleF]::new(3, 16, 26, 13)
        )
        $graphics.SetClip(
            $clip,
            [System.Drawing.Drawing2D.CombineMode]::Replace
        )

        switch ($Shape) {
            "Circle"   { $graphics.FillEllipse($brush, 4, 4, 24, 24) }
            "Square"   { $graphics.FillRectangle($brush, 5, 5, 22, 22) }
            "Triangle" { $graphics.FillPolygon($brush, $triangle) }
        }

        $graphics.ResetClip()
        $clip.Dispose()
    }
    elseif ($null -ne $brush) {
        switch ($Shape) {
            "Circle"   { $graphics.FillEllipse($brush, 4, 4, 24, 24) }
            "Square"   { $graphics.FillRectangle($brush, 5, 5, 22, 22) }
            "Triangle" { $graphics.FillPolygon($brush, $triangle) }
        }
    }

    switch ($Shape) {
        "Circle"   { $graphics.DrawEllipse($outline, 4, 4, 24, 24) }
        "Square"   { $graphics.DrawRectangle($outline, 5, 5, 22, 22) }
        "Triangle" { $graphics.DrawPolygon($outline, $triangle) }
    }

    $handle = $bitmap.GetHicon()
    $icon = [System.Drawing.Icon]::FromHandle($handle).Clone()
    [void][MarketOSNativeIconV3]::DestroyIcon($handle)

    if ($null -ne $brush) { $brush.Dispose() }
    $outline.Dispose()
    $graphics.Dispose()
    $bitmap.Dispose()

    return $icon
}

function Format-RunDuration {
    param([TimeSpan]$Duration)

    if ($Duration.TotalSeconds -lt 0) {
        $Duration = [TimeSpan]::Zero
    }

    if ($Duration.Days -gt 0) {
        return "{0}天 {1}小时{2}分" -f $Duration.Days, $Duration.Hours, $Duration.Minutes
    }

    if ($Duration.Hours -gt 0) {
        return "{0}小时{1}分" -f $Duration.Hours, $Duration.Minutes
    }

    return "{0}分" -f $Duration.Minutes
}

function Save-DisconnectedAt {
    param(
        [string]$TaskName,
        [DateTime]$Time
    )

    Set-ItemProperty `
        -Path $stateRegistryPath `
        -Name $TaskName `
        -Value $Time.ToString("o") `
        -Force
}

function Read-DisconnectedAt {
    param([string]$TaskName)

    try {
        $raw = (Get-ItemProperty `
            -Path $stateRegistryPath `
            -Name $TaskName `
            -ErrorAction Stop).$TaskName

        return [DateTime]::Parse([string]$raw)
    }
    catch {
        return $null
    }
}

function Set-NotifyText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return "MarketOS：状态未知"
    }

    if ($Text.Length -le 63) {
        return $Text
    }

    return $Text.Substring(0, 62) + "…"
}

function Get-DynamicProperty {
    param(
        $Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }

    return $property.Value
}

function Get-MarketTaskStatus {
    param(
        $Indicator,
        $Snapshot
    )

    try {
        if ($null -eq $Snapshot) {
            throw "后台状态尚未返回"
        }

        $task = Get-DynamicProperty `
            -Object $Snapshot.tasks `
            -Name $Indicator.TaskName
        if ($null -eq $task) {
            throw "计划任务状态读取失败"
        }

        $taskRunning = [bool]$task.running
        $matchingProcess = if ($Indicator.TaskName -eq "MarketOS-Gateway") {
            $Snapshot.processes.gateway
        }
        else {
            $null
        }
        $running = if ([string]::IsNullOrWhiteSpace($Indicator.ProcessPattern)) {
            $taskRunning
        }
        else {
            ($null -ne $matchingProcess) -and [bool]$matchingProcess.running
        }

        if ($running) {
            $startedAtText = if (($null -ne $matchingProcess) -and
                (-not [string]::IsNullOrWhiteSpace([string]$matchingProcess.startedAt))) {
                [string]$matchingProcess.startedAt
            }
            else {
                [string]$task.lastRunTime
            }
            $startedAt = if ([string]::IsNullOrWhiteSpace($startedAtText)) {
                Get-Date
            }
            else {
                [DateTime]::Parse($startedAtText)
            }
            if (($null -eq $startedAt) -or ($startedAt.Year -lt 2000)) {
                $startedAt = Get-Date
            }

            $Indicator.DisconnectedAt = $null
            $duration = Format-RunDuration -Duration ((Get-Date) - $startedAt)

            return [pscustomobject]@{
                Health = "Normal"
                Text   = "正常运行 $duration"
                Running = $true
                TaskRunning = $taskRunning
            }
        }

        if ($null -eq $Indicator.DisconnectedAt) {
            if ($Indicator.LastRunning -eq $true) {
                $Indicator.DisconnectedAt = Get-Date
                Save-DisconnectedAt `
                    -TaskName $Indicator.TaskName `
                    -Time $Indicator.DisconnectedAt
            }
            else {
                $saved = Read-DisconnectedAt -TaskName $Indicator.TaskName
                if ($null -ne $saved) {
                    $Indicator.DisconnectedAt = $saved
                }
                else {
                    $Indicator.DisconnectedAt = Get-Date
                    Save-DisconnectedAt `
                        -TaskName $Indicator.TaskName `
                        -Time $Indicator.DisconnectedAt
                }
            }
        }

        return [pscustomobject]@{
            Health = "Abnormal"
            Text   = "断开时间 $($Indicator.DisconnectedAt.ToString('yyyy/MM/dd HH:mm'))"
            Running = $false
            TaskRunning = $false
        }
    }
    catch {
        return [pscustomobject]@{
            Health = "Unknown"
            Text   = "状态读取失败"
            Running = $false
            TaskRunning = $false
        }
    }
}

function Get-AmarketCollectionStatus {
    param(
        $Indicator,
        $Snapshot
    )

    try {
        $Probe = $Snapshot.probe
        if ($null -eq $Probe) {
            throw "Amarket 状态探针尚未返回数据"
        }

        $taskResults = @{}
        $anyTaskRunning = $false
        foreach ($taskName in $Indicator.TaskNames) {
            $task = Get-DynamicProperty `
                -Object $Snapshot.tasks `
                -Name $taskName
            if ($null -eq $task) {
                throw "计划任务状态读取失败"
            }
            if ([bool]$task.running) {
                $anyTaskRunning = $true
            }

            $label = switch ($taskName) {
                "HithinkSnapshotCollector" { "快照采集" }
                "HithinkSectorMappingSync" { "板块同步" }
                "HithinkDailyPipeline" { "日K采集" }
            }
            $normal = $false
            $stateText = "任务不存在"

            if ([bool]$task.exists) {
                if ($taskName -eq "HithinkSnapshotCollector") {
                    $processRunning = [bool]$Snapshot.processes.collector.running
                    if ([bool]$task.running -and $processRunning) {
                        $normal = $true
                        $stateText = "运行中"
                    }
                    else {
                        $stateText = "进程已停止"
                    }
                }
                elseif ([bool]$task.running) {
                    $normal = $true
                    $stateText = "运行中"
                }
                else {
                    if (($task.state -eq "Ready") -and
                        ($task.lastTaskResult -eq 0)) {
                        $normal = $true
                        $stateText = "待命，上次成功"
                    }
                    else {
                        $stateText = "异常"
                    }
                }
            }

            $taskResults[$taskName] = [pscustomobject]@{
                label = $label
                state = $stateText
                normal = $normal
                text = "$label：$stateText"
            }
        }

        $Probe | Add-Member `
            -MemberType NoteProperty `
            -Name tasks `
            -Value ([pscustomobject]$taskResults) `
            -Force

        $clickhouseNormal = $Probe.overall_health -eq "Normal"
        $snapshotNormal = $taskResults.HithinkSnapshotCollector.normal
        $otherTasksNormal = (
            $taskResults.HithinkSectorMappingSync.normal -and
            $taskResults.HithinkDailyPipeline.normal
        )
        $health = if ((-not $snapshotNormal) -or (-not $clickhouseNormal)) {
            # 快照服务异常或无法读取快照进度：圆圈全空心。
            "Unknown"
        }
        elseif (-not $otherTasksNormal) {
            # 快照正常，但板块同步或日K任一异常：圆圈半空心。
            "Partial"
        }
        else {
            "Normal"
        }
        return [pscustomobject]@{
            Health = $health
            Text = [string]$Probe.tooltip
            Running = $snapshotNormal
            TaskRunning = $anyTaskRunning
            Probe = $Probe
        }
    }
    catch {
        return [pscustomobject]@{
            Health = "Unknown"
            Text = "个股快照行情：状态读取失败"
            Running = $false
            TaskRunning = $false
            Probe = $null
        }
    }
}

$script:AmarketProbeProcess = $null
$script:AmarketProbeStartedAt = $null
$script:AmarketProbeResult = $null
$script:AmarketProbeNextStart = [DateTime]::MinValue

function New-AmarketProbeFailure {
    param([string]$Text)

    return [pscustomobject]@{
        probe = [pscustomobject]@{
            overall_health = "Unknown"
            tooltip = $Text
            clickhouse = [pscustomobject]@{
                normal = $false
                text = "ClickHouse：状态未知"
            }
        }
        tasks = [pscustomobject]@{}
        processes = [pscustomobject]@{}
    }
}

function Start-AmarketProbe {
    if ($null -ne $script:AmarketProbeProcess) {
        return
    }

    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
        $startInfo.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "D:\Amarket\tools\MarketOS-TrayProbe.ps1"'
        $startInfo.WorkingDirectory = "D:\Amarket"
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $script:AmarketProbeProcess = [System.Diagnostics.Process]::Start($startInfo)
        $script:AmarketProbeStartedAt = Get-Date
    }
    catch {
        $script:AmarketProbeProcess = $null
        $script:AmarketProbeStartedAt = $null
        $script:AmarketProbeResult = New-AmarketProbeFailure `
            -Text "个股快照行情：状态检查无法启动"
        $script:AmarketProbeNextStart = (Get-Date).AddSeconds(15)
    }
}

function Complete-AmarketProbe {
    if ($null -eq $script:AmarketProbeProcess) {
        return $false
    }

    $timedOut = (((Get-Date) - $script:AmarketProbeStartedAt).TotalSeconds -ge 10)
    if ((-not $script:AmarketProbeProcess.HasExited) -and (-not $timedOut)) {
        return $false
    }

    try {
        if ($timedOut -and (-not $script:AmarketProbeProcess.HasExited)) {
            $script:AmarketProbeProcess.Kill()
            [void]$script:AmarketProbeProcess.WaitForExit(1000)
            $script:AmarketProbeResult = New-AmarketProbeFailure `
                -Text "个股快照行情：状态读取超时"
        }
        else {
            $standardOutput = $script:AmarketProbeProcess.StandardOutput.ReadToEnd()
            $raw = $standardOutput -split "`r?`n" |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -Last 1

            if ([string]::IsNullOrWhiteSpace([string]$raw)) {
                throw "Amarket 状态探针没有返回数据"
            }
            $script:AmarketProbeResult = [string]$raw |
                ConvertFrom-Json -ErrorAction Stop
        }
    }
    catch {
        $script:AmarketProbeResult = New-AmarketProbeFailure `
            -Text "个股快照行情：状态读取失败"
    }
    finally {
        $script:AmarketProbeProcess.Dispose()
        $script:AmarketProbeProcess = $null
        $script:AmarketProbeStartedAt = $null
        $script:AmarketProbeNextStart = (Get-Date).AddSeconds(15)
    }

    return $true
}

$indicators = @(
    [pscustomobject]@{
        Label          = "采集"
        TaskName       = "HithinkSnapshotCollector"
        TaskNames      = @(
            "HithinkSnapshotCollector",
            "HithinkSectorMappingSync",
            "HithinkDailyPipeline"
        )
        ProcessPattern = "app\.hithink\.cli\s+serve"
        Shape          = "Circle"
        Notify         = [System.Windows.Forms.NotifyIcon]::new()
        Menu           = $null
        ConnectItem    = $null
        DisconnectItem = $null
        DisconnectedAt = $null
        LastRunning    = $null
        LastHealth     = $null
        CaptureItem    = $null
        UniverseItem   = $null
        DailyKItem     = $null
    },
    [pscustomobject]@{
        Label          = "网关"
        TaskName       = "MarketOS-Gateway"
        ProcessPattern = "D:\\Amarket.*-m\s+app\.ai_gateway"
        Shape          = "Square"
        Notify         = [System.Windows.Forms.NotifyIcon]::new()
        Menu           = $null
        ConnectItem    = $null
        DisconnectItem = $null
        DisconnectedAt = $null
        LastRunning    = $null
        LastHealth     = $null
        CaptureItem    = $null
        UniverseItem   = $null
        DailyKItem     = $null
    },
    [pscustomobject]@{
        Label          = "隧道"
        TaskName       = "MarketOS-Tunnel"
        ProcessPattern = $null
        Shape          = "Triangle"
        Notify         = [System.Windows.Forms.NotifyIcon]::new()
        Menu           = $null
        ConnectItem    = $null
        DisconnectItem = $null
        DisconnectedAt = $null
        LastRunning    = $null
        LastHealth     = $null
        CaptureItem    = $null
        UniverseItem   = $null
        DailyKItem     = $null
    }
)

function Update-Indicator {
    param($Indicator)

    $result = if ($Indicator.Shape -eq "Circle") {
        Get-AmarketCollectionStatus `
            -Indicator $Indicator `
            -Snapshot $script:AmarketProbeResult
    }
    else {
        Get-MarketTaskStatus `
            -Indicator $Indicator `
            -Snapshot $script:AmarketProbeResult
    }

    if (($null -eq $Indicator.LastRunning) -or
        ($Indicator.LastRunning -ne $result.Running) -or
        ($Indicator.LastHealth -ne $result.Health)) {
        $newIcon = New-ShapeIcon `
            -Shape $Indicator.Shape `
            -Status $result.Health

        $oldIcon = $Indicator.Notify.Icon
        $Indicator.Notify.Icon = $newIcon

        if ($null -ne $oldIcon) {
            $oldIcon.Dispose()
        }
    }

    $Indicator.Notify.Text = Set-NotifyText `
        -Text "$($Indicator.Label)：$($result.Text)"
    $Indicator.ConnectItem.Enabled = -not $result.Running
    $Indicator.DisconnectItem.Enabled = $result.TaskRunning

    if (($Indicator.Shape -eq "Circle") -and
        ($null -ne $result.Probe)) {
        if ($null -ne $result.Probe.tasks.HithinkSnapshotCollector) {
            $Indicator.CaptureItem.Text = [string]$result.Probe.tasks.HithinkSnapshotCollector.text
        }
        if ($null -ne $result.Probe.tasks.HithinkSectorMappingSync) {
            $Indicator.UniverseItem.Text = [string]$result.Probe.tasks.HithinkSectorMappingSync.text
        }
        if ($null -ne $result.Probe.tasks.HithinkDailyPipeline) {
            $Indicator.DailyKItem.Text = [string]$result.Probe.tasks.HithinkDailyPipeline.text
        }
    }

    $Indicator.LastRunning = $result.Running
    $Indicator.LastHealth = $result.Health
}

function Update-SystemIndicators {
    foreach ($indicator in $indicators) {
        if ($indicator.Shape -ne "Circle") {
            Update-Indicator -Indicator $indicator
        }
    }
}

function Update-CollectionIndicator {
    $collectionIndicator = $indicators |
        Where-Object { $_.Shape -eq "Circle" } |
        Select-Object -First 1
    if ($null -ne $collectionIndicator) {
        Update-Indicator -Indicator $collectionIndicator
    }
}

$script:ExitRequested = $false

foreach ($indicator in $indicators) {
    $menu = [System.Windows.Forms.ContextMenuStrip]::new()
    $connectItem = $menu.Items.Add("连接")
    $disconnectItem = $menu.Items.Add("断开")
    [void]$menu.Items.Add([System.Windows.Forms.ToolStripSeparator]::new())

    if ($indicator.Shape -eq "Circle") {
        $captureItem = $menu.Items.Add("快照采集：读取中")
        $universeItem = $menu.Items.Add("板块同步：读取中")
        $dailyKItem = $menu.Items.Add("日K采集：读取中")
        $captureItem.Enabled = $false
        $universeItem.Enabled = $false
        $dailyKItem.Enabled = $false
        [void]$menu.Items.Add([System.Windows.Forms.ToolStripSeparator]::new())

        $indicator.CaptureItem = $captureItem
        $indicator.UniverseItem = $universeItem
        $indicator.DailyKItem = $dailyKItem
    }

    $refreshItem = $menu.Items.Add("立即刷新")
    $exitItem = $menu.Items.Add("退出状态显示")

    $indicator.Menu = $menu
    $indicator.ConnectItem = $connectItem
    $indicator.DisconnectItem = $disconnectItem

    $capturedIndicator = $indicator

    $connectItem.Add_Click({
        try {
            if ($capturedIndicator.Shape -eq "Circle") {
                foreach ($taskName in $capturedIndicator.TaskNames) {
                    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
                }
            }
            else {
                Start-ScheduledTask -TaskName $capturedIndicator.TaskName -ErrorAction Stop
            }
        }
        catch {
            [System.Windows.Forms.MessageBox]::Show(
                "无法连接 $($capturedIndicator.Label)：$($_.Exception.Message)",
                "MarketOS",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            ) | Out-Null
        }
        if ($capturedIndicator.Shape -eq "Circle") {
            $script:AmarketProbeNextStart = [DateTime]::MinValue
            Start-AmarketProbe
        }
        Update-Indicator -Indicator $capturedIndicator
    }.GetNewClosure())

    $disconnectItem.Add_Click({
        try {
            if ($capturedIndicator.Shape -eq "Circle") {
                foreach ($taskName in $capturedIndicator.TaskNames) {
                    Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
                }
            }
            else {
                Stop-ScheduledTask -TaskName $capturedIndicator.TaskName -ErrorAction Stop
            }
            $capturedIndicator.DisconnectedAt = Get-Date
            Save-DisconnectedAt `
                -TaskName $capturedIndicator.TaskName `
                -Time $capturedIndicator.DisconnectedAt
        }
        catch {
            [System.Windows.Forms.MessageBox]::Show(
                "无法断开 $($capturedIndicator.Label)：$($_.Exception.Message)",
                "MarketOS",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            ) | Out-Null
        }
        if ($capturedIndicator.Shape -eq "Circle") {
            $script:AmarketProbeNextStart = [DateTime]::MinValue
            Start-AmarketProbe
        }
        Update-Indicator -Indicator $capturedIndicator
    }.GetNewClosure())

    $refreshItem.Add_Click({
        if ($capturedIndicator.Shape -eq "Circle") {
            $script:AmarketProbeNextStart = [DateTime]::MinValue
            Start-AmarketProbe
        }
        Update-Indicator -Indicator $capturedIndicator
    }.GetNewClosure())

    $exitItem.Add_Click({
        $script:ExitRequested = $true
        [System.Windows.Forms.Application]::Exit()
    })

    $indicator.Notify.Icon = New-ShapeIcon `
        -Shape $indicator.Shape `
        -Status "Unknown"
    $indicator.Notify.Text = Set-NotifyText `
        -Text "$($indicator.Label)：状态未知"
    $indicator.Notify.ContextMenuStrip = $menu
    $indicator.Notify.Visible = $true
}

$timer = [System.Windows.Forms.Timer]::new()
$timer.Interval = 250
$timer.Add_Tick({
    $now = Get-Date

    if (Complete-AmarketProbe) {
        Update-CollectionIndicator
        Update-SystemIndicators
    }
    if (($null -eq $script:AmarketProbeProcess) -and
        ($now -ge $script:AmarketProbeNextStart)) {
        Start-AmarketProbe
    }
})

Update-CollectionIndicator
Update-SystemIndicators
Start-AmarketProbe
$timer.Start()

try {
    [System.Windows.Forms.Application]::Run()
}
finally {
    $timer.Stop()
    $timer.Dispose()

    if ($null -ne $script:AmarketProbeProcess) {
        try {
            if (-not $script:AmarketProbeProcess.HasExited) {
                $script:AmarketProbeProcess.Kill()
                [void]$script:AmarketProbeProcess.WaitForExit(1000)
            }
        }
        finally {
            $script:AmarketProbeProcess.Dispose()
            $script:AmarketProbeProcess = $null
        }
    }

    foreach ($indicator in $indicators) {
        $indicator.Notify.Visible = $false

        if ($null -ne $indicator.Notify.Icon) {
            $indicator.Notify.Icon.Dispose()
        }

        $indicator.Notify.Dispose()
        $indicator.Menu.Dispose()
    }
}
