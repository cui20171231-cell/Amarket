param(
    [string]$ProjectRoot = 'D:\Amarket'
)

$taskName = 'HithinkDailyPipeline'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ProjectRoot\scripts\run_daily_pipeline.ps1`"" -WorkingDirectory $ProjectRoot
$calendar = New-ScheduledTaskTrigger -Daily -At 08:40
$repair = New-ScheduledTaskTrigger -Daily -At 10:00
$pipeline = New-ScheduledTaskTrigger -Daily -At 15:45
$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = 'PT1M'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId (whoami) -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($calendar, $repair, $pipeline, $boot) -Settings $settings -Principal $principal -Description 'Independent Hithink daily-K raw/event pipeline with calendar gate, repair scan and after-close retries.' -Force
Write-Output "Installed $taskName"
