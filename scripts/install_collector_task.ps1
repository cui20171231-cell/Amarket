param(
    [string]$ProjectRoot = "D:\Amarket"
)

$taskName = "HithinkSnapshotCollector"
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\run_collector.ps1`"" -WorkingDirectory $ProjectRoot
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$bootTrigger.Delay = "PT30S"
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
$taskPrincipal = New-ScheduledTaskPrincipal -UserId (whoami) -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $bootTrigger -Settings $taskSettings -Principal $taskPrincipal -Description "Always-on Hithink collector. It starts with Windows and keeps the fixed 314-node plan online." -Force
Write-Output "Installed $taskName. It starts with Windows and stays online continuously."
