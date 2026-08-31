param(
    [string]$ProjectRoot = 'D:\Amarket',
    [string]$WindowsUser = 'DESKTOP-T13G2DE\DmarketosOS'
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Installing the Linux infrastructure task requires an Administrator PowerShell window.'
}

$oldTask = Get-ScheduledTask -TaskName 'AmarketInfrastructureBootstrap' -ErrorAction SilentlyContinue
if ($null -ne $oldTask) {
    Stop-ScheduledTask -TaskName $oldTask.TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $oldTask.TaskName -Confirm:$false
}

$taskName = 'AmarketLinuxInfrastructure'
$action = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\wsl.exe' `
    -Argument '-d Ubuntu --user root --exec /usr/local/sbin/amarket-wsl-host'
$bootTrigger = New-ScheduledTaskTrigger -AtStartup
$recoveryTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $WindowsUser -LogonType S4U -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger @($bootTrigger, $recoveryTrigger) `
    -Settings $settings `
    -Principal $taskPrincipal `
    -Description 'Starts Ubuntu, Linux Docker Engine, and the A-market ClickHouse watchdog at Windows boot without requiring an interactive login.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
