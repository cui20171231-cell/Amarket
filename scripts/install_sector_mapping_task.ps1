param(
    [string]$ProjectRoot = 'D:\Amarket'
)

$taskName = 'HithinkSectorMappingSync'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\run_sector_mapping_sync.ps1`"" -WorkingDirectory $ProjectRoot
$daily = New-ScheduledTaskTrigger -Daily -At 08:20
$boot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId (whoami) -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($daily, $boot) -Settings $settings -Principal $principal -Description 'Independent daily concept, industry and style sector mapping synchronization.' -Force
Write-Output "Installed $taskName"
