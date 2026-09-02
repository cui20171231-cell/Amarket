param(
    [string]$ProjectRoot = "D:\Amarket"
)

$ErrorActionPreference = 'Stop'
$taskName = 'AmarketEmailNotifier'
$python = 'C:\Windows\py.exe'
$taskAction = New-ScheduledTaskAction `
    -Execute $python `
    -Argument '-3.11 -m app.hithink.email_notifier monitor' `
    -WorkingDirectory $ProjectRoot
$firstRun = (Get-Date).AddMinutes(1)
$repeatTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$taskSettings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $taskAction `
    -Trigger @($bootTrigger, $repeatTrigger) `
    -Settings $taskSettings `
    -Principal $taskPrincipal `
    -Description 'Independent Amarket health checks, recovery messages, 08:55 confirmation and 16:30 close summary by email.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
