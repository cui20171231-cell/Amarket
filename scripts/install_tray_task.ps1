param(
    [string]$ProjectRoot = 'D:\Amarket'
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an Administrator PowerShell window.'
}

$trayScript = Join-Path $ProjectRoot 'tools\MarketOS-TrayStatus.ps1'
if (-not (Test-Path -LiteralPath $trayScript)) {
    throw "Tray script not found: $trayScript"
}

$taskName = 'MarketOSStatus-TrayIcons'
$action = New-ScheduledTaskAction `
    -Execute 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$trayScript`"" `
    -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $identity.Name `
    -LogonType Interactive `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $taskPrincipal `
    -Description 'A-market status tray icons for the signed-in operator.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
