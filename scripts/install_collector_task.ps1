param(
    [string]$ProjectRoot = "D:\Amarket"
)

$ErrorActionPreference = 'Stop'

$taskName = "HithinkSnapshotCollector"

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

# Reinstalling the task must not leave a Python child from an older wrapper.
# This exact command-line match excludes the daily-K, sector, and Tencent jobs.
$collectorProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('py.exe', 'python.exe', 'pythonw.exe') -and
    $_.CommandLine -match 'app\.hithink\.cli\s+serve'
} | Sort-Object { if ($_.Name -eq 'py.exe') { 1 } else { 0 } }
foreach ($collectorProcess in $collectorProcesses) {
    Stop-Process -Id $collectorProcess.ProcessId -Force -ErrorAction SilentlyContinue
}

$taskAction = New-ScheduledTaskAction -Execute 'C:\Windows\py.exe' -Argument '-3 -m app.hithink.cli serve' -WorkingDirectory $ProjectRoot
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $bootTrigger -Settings $taskSettings -Principal $taskPrincipal -Description "SYSTEM-owned always-on Hithink collector with direct Python startup and one-minute failure recovery." -Force
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
Write-Output "Installed and started $taskName as one direct SYSTEM Python task."
